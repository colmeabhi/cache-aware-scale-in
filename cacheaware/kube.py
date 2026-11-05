"""Stage 2 — the few Kubernetes calls the controller needs.

Three operations, nothing more:

    list_pods(namespace, selector)   -> which vLLM pods exist, their IPs, annotations
    scrape(pod, port, path, mode)    -> that pod's /metrics page as text
    annotate(namespace, pod, {...})  -> write annotations on a pod (None removes one)

Two ways to reach a pod's metrics:

    mode="direct"   http://<pod ip>:8000/metrics
                    works inside the cluster, and from a k3s node (the node
                    can route to pod IPs). Cheapest.
    mode="proxy"    GET /api/v1/namespaces/NS/pods/NAME:8000/proxy/metrics
                    through the API server. Works from your laptop with only a
                    kubeconfig. One extra hop, fine for a few pods every few
                    seconds.

``mode="auto"`` picks direct when running in-cluster, proxy otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .vllm_metrics import fetch_text


@dataclass
class PodInfo:
    name: str
    namespace: str
    ip: str | None
    phase: str                 # Pending / Running / Succeeded / Failed / Unknown
    ready: bool
    node: str | None
    created: datetime | None
    annotations: dict[str, str] = field(default_factory=dict)
    deleting: bool = False     # deletionTimestamp set -> already on its way out


class Kube:
    def __init__(self, kubeconfig: str | None = None, context: str | None = None):
        # Imported here so stage 1 (no Kubernetes at all) never needs the package.
        from kubernetes import client, config

        try:
            config.load_incluster_config()
            self.in_cluster = True
        except config.ConfigException:
            config.load_kube_config(config_file=kubeconfig, context=context)
            self.in_cluster = False
        self.core = client.CoreV1Api()

    def list_pods(self, namespace: str, selector: str) -> list[PodInfo]:
        result = self.core.list_namespaced_pod(namespace, label_selector=selector)
        pods = []
        for item in result.items:
            conditions = item.status.conditions or []
            ready = any(c.type == "Ready" and c.status == "True" for c in conditions)
            pods.append(PodInfo(
                name=item.metadata.name,
                namespace=namespace,
                ip=item.status.pod_ip,
                phase=item.status.phase or "Unknown",
                ready=ready,
                node=item.spec.node_name,
                created=item.metadata.creation_timestamp,
                annotations=dict(item.metadata.annotations or {}),
                deleting=item.metadata.deletion_timestamp is not None,
            ))
        return sorted(pods, key=lambda p: p.name)

    def scrape(self, pod: PodInfo, port: int = 8000, path: str = "/metrics",
               mode: str = "auto", timeout: float = 3.0) -> str:
        if mode == "auto":
            mode = "direct" if self.in_cluster else "proxy"
        if mode == "direct":
            if not pod.ip:
                raise RuntimeError(f"{pod.name} has no IP yet")
            return fetch_text(f"http://{pod.ip}:{port}{path}", timeout)
        # proxy: the API server forwards the GET to the pod for us
        return self.core.connect_get_namespaced_pod_proxy_with_path(
            name=f"{pod.name}:{port}", namespace=pod.namespace, path=path.lstrip("/"),
            _request_timeout=timeout)

    def annotate(self, namespace: str, pod_name: str, annotations: dict[str, str | None]) -> None:
        """Merge-patch annotations. A value of None deletes that annotation."""
        body = {"metadata": {"annotations": annotations}}
        self.core.patch_namespaced_pod(pod_name, namespace, body)
