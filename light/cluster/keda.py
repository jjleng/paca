import pulumi
from pulumi_kubernetes.helm.v3 import Chart, ChartOpts, FetchOpts
from light.utils import call_once
import pulumi_kubernetes as k8s


@call_once
def create_keda(k8s_provider: k8s.Provider) -> None:
    """
    Installs a KEDA chart.
    """
    k8s.core.v1.Namespace(
        "keda",
        metadata={"name": "keda"},
    )
    Chart(
        "keda",
        ChartOpts(
            chart="keda",
            version="2.12.1",
            namespace="keda",
            fetch_opts=FetchOpts(repo="https://kedacore.github.io/charts"),
            values={},
        ),
        opts=pulumi.ResourceOptions(provider=k8s_provider),
    )
