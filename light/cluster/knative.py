from functools import partial
from typing import Any, Callable, Dict

import pulumi
import pulumi_kubernetes as k8s
from pulumi import ResourceOptions
from pulumi_kubernetes.apiextensions import CustomResource
from pulumi_kubernetes.yaml import ConfigFile

from light.cluster.prometheus import create_prometheus
from light.config import CloudConfig
from light.utils import call_once

VERSION = "v1.12.3"
ISTIO_VERSION = "v1.12.1"


def limit_hpa_min_replicas(args: Any, opts: pulumi.ResourceOptions) -> None:
    if (
        args["kind"] == "HorizontalPodAutoscaler"
        and args["metadata"]["name"] == "istiod"
    ):
        args["spec"]["minReplicas"] = 1


def limit_resources(args: Any, opts: pulumi.ResourceOptions) -> None:
    if args["kind"] == "Deployment" and args["metadata"]["name"] == "istiod":
        for container in args["spec"]["template"]["spec"]["containers"]:
            if container["name"] == "discovery":
                container["resources"] = {
                    "requests": {"cpu": "300m", "memory": "1Gi"},
                }


def limit_deployment_replicas(args: Any, opts: pulumi.ResourceOptions) -> None:
    if (
        args["kind"] == "Deployment"
        and args["metadata"]["name"] == "istio-ingressgateway"
    ):
        args["spec"]["replicas"] = 2
        for container in args["spec"]["template"]["spec"]["containers"]:
            if container["name"] == "istio-proxy":
                container["resources"] = {
                    "requests": {"cpu": "500m", "memory": "500Mi"},
                }


def crd_resources(labels: Dict[str, Any]) -> bool:
    return labels.get("knative.dev/crd-install") == "true"


def non_crd_resources(labels: Dict[str, Any]) -> bool:
    return labels.get("knative.dev/crd-install") != "true"


def crd_install_filter(
    inputs: Any, opts: ResourceOptions, filter: Callable[[Any], bool]
) -> None:
    if "metadata" in inputs:
        if not filter(inputs["metadata"].get("labels", {})):
            inputs["kind"] = "List"
            inputs["items"] = []


def exclude_knative_eventing_namespace(inputs: Any, opts: ResourceOptions) -> None:
    if "metadata" in inputs and inputs["metadata"]["namespace"] == "knative-eventing":
        inputs["kind"] = "List"
        inputs["items"] = []


only_crd_transform = partial(crd_install_filter, filter=crd_resources)
non_crd_transform = partial(crd_install_filter, filter=non_crd_resources)


@call_once
def create_knative_and_istio(config: CloudConfig, k8s_provider: k8s.Provider) -> None:
    yaml_files = [
        # TODO: sigstore verification
        # Creates resources under the knative-serving namespace
        f"https://github.com/knative/serving/releases/download/knative-{VERSION}/serving-core.yaml",
    ]
    for i, yaml_file in enumerate(yaml_files):
        ConfigFile(
            yaml_file.split("/")[-1],
            file=yaml_file,
            opts=pulumi.ResourceOptions(provider=k8s_provider),
        )

    yaml_file = f"https://github.com/knative/net-istio/releases/download/knative-{ISTIO_VERSION}/istio.yaml"
    istio_crd_install = ConfigFile(
        "istio-crd-install",
        file=yaml_file,
        transformations=[only_crd_transform],
        opts=pulumi.ResourceOptions(provider=k8s_provider),
    )

    istio_full_install = ConfigFile(
        "istio-non-crd-install",
        file=yaml_file,
        transformations=[
            non_crd_transform,
            limit_resources,
            limit_hpa_min_replicas,
            limit_deployment_replicas,
        ],
        opts=pulumi.ResourceOptions(
            provider=k8s_provider, depends_on=[istio_crd_install]
        ),
    )

    yaml_file = f"https://github.com/knative/net-istio/releases/download/knative-{ISTIO_VERSION}/net-istio.yaml"
    net_istio = ConfigFile(
        "net-istio",
        file=yaml_file,
        opts=pulumi.ResourceOptions(
            provider=k8s_provider, depends_on=[istio_full_install]
        ),
    )

    yaml_file = f"https://github.com/knative/serving/releases/download/knative-{VERSION}/serving-default-domain.yaml"
    ConfigFile(
        "kn-default-domain",
        file=yaml_file,
        opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[net_istio]),
    )

    # Now, install ServiceMonitor and PodMonitor CRDs
    prometheus = create_prometheus(config, k8s_provider)

    if not prometheus:
        return

    yaml_file = "https://raw.githubusercontent.com/knative-extensions/monitoring/main/servicemonitor.yaml"

    ConfigFile(
        "kn-prom-monitor",
        file=yaml_file,
        transformations=[exclude_knative_eventing_namespace],
        opts=pulumi.ResourceOptions(provider=k8s_provider, depends_on=[prometheus]),
    )

    CustomResource(
        "ingressgateway-monitor",
        api_version="monitoring.coreos.com/v1",
        kind="ServiceMonitor",
        metadata={
            "name": "ingressgateway-monitor",
            # ServiceMonitor can be discovered regardless of namespace.
            # See `serviceMonitorSelectorNilUsesHelmValues` and
            # `podMonitorSelectorNilUsesHelmValues` in the Prometheus chart.
            # We can create this in the istio-system namespace.
            "namespace": "istio-system",
        },
        spec={
            "selector": {
                "matchLabels": {
                    "app": "istio-ingressgateway",
                }
            },
            "namespaceSelector": {"matchNames": ["istio-system"]},
            "endpoints": [
                {
                    "port": "http-envoy-prom",
                    "path": "/stats/prometheus",
                    "interval": "15s",
                },
            ],
        },
        opts=pulumi.ResourceOptions(
            provider=k8s_provider, depends_on=[prometheus, net_istio]
        ),
    )
