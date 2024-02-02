import contextlib
import os
import re
import select
import socket
import threading
import time
from functools import partial
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    Tuple,
    TypeAlias,
)

from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
from kubernetes.stream import portforward
from ruamel.yaml import YAML

from light.logger import logger
from light.utils import get_project_data_dir, read_yaml_file

KubernetesResourceKind: TypeAlias = Literal[
    "Deployment",
    "Service",
    "HorizontalPodAutoscaler",
    "ScaledObject",
    "TriggerAuthentication",
    "ServiceAccount",
    "Secret",
    "RoleBinding",
    "ConfigMap",
    "Role",
    "Package",
    "Environment",
    "Function",
    "Gateway",
    "VirtualService",
    "ServiceMonitor",
]


class CustomResource:
    def __init__(
        self,
        api_version: str,
        kind: KubernetesResourceKind,
        plural: str,
        spec: Dict[str, Any],
        metadata: client.V1ObjectMeta,
        status: Optional[Dict[str, Any]] = None,
    ):
        # Ensure api_version is in the format group/version
        if not re.match(r"^.+/v[\w]+$", api_version):
            raise ValueError("api_version must be in the format 'group/version'")
        self.api_version = api_version
        self.group, self.version = api_version.split("/")
        self.kind = kind
        self.plural = plural
        self.metadata = metadata
        self.spec = spec
        self.status = status


def create_namespaced_custom_object(namespace: str, resource: CustomResource) -> Any:
    body = {
        "apiVersion": resource.api_version,
        "kind": resource.kind,
        "metadata": {
            **resource.metadata.to_dict(),
            "name": resource.metadata.name,
            "namespace": namespace,
        },
        "spec": resource.spec,
    }

    if resource.status is not None:
        body["status"] = resource.status

    api_instance = client.CustomObjectsApi()

    return api_instance.create_namespaced_custom_object(
        group=resource.group,
        version=resource.version,
        namespace=namespace,
        plural=resource.plural,
        body=body,
    )


def read_namespaced_custom_object(
    name: str, namespace: str, resource: CustomResource
) -> Any:
    api_instance = client.CustomObjectsApi()
    return api_instance.get_namespaced_custom_object(
        group=resource.group,
        version=resource.version,
        namespace=namespace,
        plural=resource.plural,
        name=name,
    )


def replace_namespaced_custom_object(
    name: str, namespace: str, resource: CustomResource
) -> Any:
    res = read_namespaced_custom_object(name, namespace, resource)
    body = {
        "apiVersion": resource.api_version,
        "kind": resource.kind,
        "metadata": {
            **resource.metadata.to_dict(),
            "name": resource.metadata.name,
            "namespace": namespace,
            "resourceVersion": res["metadata"]["resourceVersion"],
        },
        "spec": resource.spec,
    }
    api_instance = client.CustomObjectsApi()
    return api_instance.replace_namespaced_custom_object(
        group=resource.group,
        version=resource.version,
        namespace=namespace,
        plural=resource.plural,
        name=name,
        body=body,
    )


def delete_namespaced_custom_object(
    name: str, namespace: str, resource: CustomResource
) -> Any:
    api_instance = client.CustomObjectsApi()
    return api_instance.delete_namespaced_custom_object(
        group=resource.group,
        version=resource.version,
        namespace=namespace,
        plural=resource.plural,
        name=name,
    )


def list_namespaced_custom_object(namespace: str, resource: CustomResource) -> Any:
    api_instance = client.CustomObjectsApi()
    custom_resources = api_instance.list_namespaced_custom_object(
        group=resource.group,
        version=resource.version,
        namespace=namespace,
        plural=resource.plural,
    )
    return custom_resources.get("items", [])


class KubernetesResource(Protocol):
    metadata: client.V1ObjectMeta
    kind: KubernetesResourceKind


def apply_resource(
    resource: KubernetesResource,
) -> Any:
    """
    Applies a Kubernetes resource by creating or updating it.

    Args:
        resource (KubernetesResource): The Kubernetes resource to apply.

    Returns:
        Any: The response from the API call.

    Raises:
        ValueError: If the resource kind is unsupported.
        ApiException: If an error occurs while creating or updating the resource.
    """

    # Determine the resource kind and prepare the appropriate API client
    kind = resource.kind
    namespace = resource.metadata.namespace
    if not namespace:
        raise ValueError("Namespace is required")

    if kind == "Deployment":
        api = client.AppsV1Api()
        create_method = api.create_namespaced_deployment
        replace_method = api.replace_namespaced_deployment
        read_method = api.read_namespaced_deployment
    elif kind == "Service":
        api = client.CoreV1Api()
        create_method = api.create_namespaced_service
        replace_method = api.replace_namespaced_service
        read_method = api.read_namespaced_service
    elif kind == "HorizontalPodAutoscaler":
        api = client.AutoscalingV2Api()
        create_method = api.create_namespaced_horizontal_pod_autoscaler
        replace_method = api.replace_namespaced_horizontal_pod_autoscaler
        read_method = api.read_namespaced_horizontal_pod_autoscaler
    elif kind in [
        "ScaledObject",
        "TriggerAuthentication",
        "Package",
        "Environment",
        "Function",
        "Gateway",
        "VirtualService",
        "ServiceMonitor",
    ]:
        create_method = create_namespaced_custom_object
        replace_method = replace_namespaced_custom_object
        read_method = partial(read_namespaced_custom_object, resource=resource)
    elif kind == "ServiceAccount":
        api = client.CoreV1Api()
        create_method = api.create_namespaced_service_account
        replace_method = api.patch_namespaced_service_account
        read_method = api.read_namespaced_service_account
    elif kind == "Secret":
        api = client.CoreV1Api()
        create_method = api.create_namespaced_secret
        replace_method = api.patch_namespaced_secret
        read_method = api.read_namespaced_secret
    elif kind == "RoleBinding":
        api = client.RbacAuthorizationV1Api()
        create_method = api.create_namespaced_role_binding
        replace_method = api.patch_namespaced_role_binding
        read_method = api.read_namespaced_role_binding
    elif kind == "Role":
        api = client.RbacAuthorizationV1Api()
        create_method = api.create_namespaced_role
        replace_method = api.patch_namespaced_role
        read_method = api.read_namespaced_role
    elif kind == "ConfigMap":
        api = client.CoreV1Api()
        create_method = api.create_namespaced_config_map
        replace_method = api.patch_namespaced_config_map
        read_method = api.read_namespaced_config_map
    else:
        raise ValueError(f"Unsupported kind: {kind}")

    # Try to read (get) the resource; if it exists, replace it, otherwise create it
    try:
        read_method(resource.metadata.name, namespace)
        response = replace_method(resource.metadata.name, namespace, resource)
        logger.info(f"{kind} '{resource.metadata.name}' updated.")
    except ApiException as e:
        if e.status == 404:
            response = create_method(namespace, resource)
            logger.info(f"{kind} '{resource.metadata.name}' created.")
        else:
            raise e
    return response


def create_namespace(name: str) -> None:
    """
    Creates a Kubernetes namespace.

    Args:
        name (str): The name of the namespace to create.

    Returns:
        None

    Raises:
        ApiException: If an error occurs while creating the namespace.
    """
    api = client.CoreV1Api()
    namespace = client.V1Namespace(metadata=client.V1ObjectMeta(name=name))
    try:
        api.create_namespace(body=namespace)
    except ApiException as e:
        if e.status == 409:  # Conflict, namespace already exists
            pass
        else:
            raise


def create_service_account(namespace: str, account_name: str) -> None:
    service_account = client.V1ServiceAccount(
        kind="ServiceAccount",
        metadata=client.V1ObjectMeta(name=account_name, namespace=namespace),
    )
    apply_resource(service_account)


def create_role_binding(
    binding_namespace: str,
    binding_name: str,
    role_name: str,
    subject_namespace: str,
    service_account_name: str,
) -> None:
    role_binding = client.V1RoleBinding(
        kind="RoleBinding",
        metadata=client.V1ObjectMeta(name=binding_name, namespace=binding_namespace),
        subjects=[
            client.V1Subject(
                kind="ServiceAccount",
                name=service_account_name,
                namespace=subject_namespace,
            )
        ],
        role_ref=client.V1RoleRef(
            api_group="rbac.authorization.k8s.io", kind="Role", name=role_name
        ),
    )
    apply_resource(role_binding)


def create_config_map(namespace: str, map_name: str, data: Dict[str, Any]) -> None:
    config_map = client.V1ConfigMap(
        kind="ConfigMap",
        metadata=client.V1ObjectMeta(name=map_name, namespace=namespace),
        data=data,
    )
    apply_resource(config_map)


def create_role(
    namespace: str, role_name: str, rules: List[client.V1PolicyRule]
) -> None:
    role = client.V1Role(
        api_version="rbac.authorization.k8s.io/v1",
        kind="Role",
        metadata=client.V1ObjectMeta(name=role_name, namespace=namespace),
        rules=rules,
    )
    apply_resource(role)


def is_ready_pod(pod: Any) -> bool:
    for condition in pod.status.conditions:
        if condition.type == "Ready" and condition.status == "True":
            return True
    return False


def find_free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def run_port_forward(
    label_selector: str,
    local_port: int,
    container_port: int,
    namespace: Optional[str] = None,
) -> Tuple[threading.Event, Callable[[], None]]:
    v1 = client.CoreV1Api()

    if namespace is None:
        namespace = ""

    pods = v1.list_namespaced_pod(namespace, label_selector=label_selector)

    if len(pods.items) == 0:
        raise Exception(
            f"No available pod for port-forwarding with label selector {label_selector}"
        )

    namespaces: Dict[str, Any] = {}
    ns_list = []

    for pod in pods.items:
        if pod.metadata.namespace not in namespaces:
            namespaces[pod.metadata.namespace] = []
            ns_list.append(pod.metadata.namespace)
        namespaces[pod.metadata.namespace].append(pod)

    if len(ns_list) > 1:
        raise Exception(
            f"Found pods in {len(namespaces)} namespaces, {', '.join(ns_list)}. Please specify a namespace."
        )

    ns = ns_list[0]
    pods = namespaces.get(ns)

    if pods is None:
        raise Exception(f"Error finding pods within the given namespace {ns}")

    pod_name = None
    pod_namespace = None

    for pod in pods:
        if is_ready_pod(pod):
            pod_name = pod.metadata.name
            pod_namespace = pod.metadata.namespace
            break

    if pod_name is None or pod_namespace is None:
        raise Exception(
            f"No ready pod for port-forwarding with label selector {label_selector}"
        )

    ready_event = threading.Event()
    stop_event = threading.Event()

    def _proxy(
        client_socket: socket.socket,
        unix_socket: socket.socket,
        socket_lock: threading.Lock,
    ) -> None:
        try:
            while not stop_event.is_set():
                # Wait for data to be available on either socket
                readable, _, _ = select.select([client_socket, unix_socket], [], [])

                for sock in readable:
                    with socket_lock:
                        data = sock.recv(4096)

                    if not data:
                        # If the connection was closed, stop the proxy
                        return

                    # Determine the target socket
                    target_socket = (
                        unix_socket if sock is client_socket else client_socket
                    )

                    # Forward the data to the other socket
                    with socket_lock:
                        target_socket.sendall(data)

        except Exception as e:
            pass
        finally:
            client_socket.close()

    def _run_forward() -> None:
        pf = portforward(
            v1.connect_get_namespaced_pod_portforward,
            pod_name,
            pod_namespace,
            ports=str(container_port),
        )

        while not pf.connected:
            time.sleep(0.1)

        unix_socket = pf.socket(container_port)

        inet_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        inet_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        inet_socket.bind(("localhost", local_port))
        inet_socket.listen(5)
        inet_socket.settimeout(1)

        ready_event.set()

        lock = threading.Lock()

        try:
            while not stop_event.is_set():
                try:
                    # Accept new client connections
                    client_socket, addr = inet_socket.accept()
                    logger.debug(f"New connection from {addr}")

                    # Start a new thread to handle this client
                    threading.Thread(
                        target=_proxy, args=(client_socket, unix_socket, lock)
                    ).start()
                except socket.timeout:
                    # The accept call timed out, just loop again
                    continue
        finally:
            inet_socket.close()

        pf.close()

    forward_thread = threading.Thread(target=_run_forward)
    forward_thread.start()

    def stop_port_forward() -> None:
        stop_event.set()
        forward_thread.join()

    return ready_event, stop_port_forward


def setup_port_forward(
    label_selector: str, namespace: str, container_port: int
) -> Tuple[str, Callable[[], None]]:
    local_port = find_free_port()

    max_duration = 2

    ready_event, stop_forward = run_port_forward(
        label_selector, local_port, container_port, namespace
    )

    ready_event.wait()

    logger.debug(f"Waiting for port forward {local_port} to start...")

    wait_duration = 0.05
    while True:
        try:
            with socket.create_connection(
                ("localhost", local_port), timeout=wait_duration
            ):
                break
        except:
            logger.debug(f"Error dialing on local port {local_port}")
            time.sleep(wait_duration)
            wait_duration *= 2
            if wait_duration > max_duration:
                wait_duration = max_duration

    logger.debug(f"Port forward from local port {local_port} started")

    return str(local_port), stop_forward


def try_load_kubeconfig() -> bool:
    try:
        config.load_kube_config()
        return True
    except Exception as e:
        logger.debug(f"Error loading kubeconfig: {e}")
        return False


class KubeconfigMerger:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}

    def _entries_by_key(self, key: str) -> List[Any]:
        self.config[key] = self.config.get(key) or []
        entries = self.config[key]
        if not isinstance(entries, list):
            raise Exception(
                f"Tried to insert into {key}, "
                f"which is a {type(entries)} "
                f"not a list."
            )
        return entries

    def _index_same_name(
        self, entries: List[Any], new_entry: Dict[str, Any]
    ) -> Optional[int]:
        if "name" in new_entry:
            name_to_search = new_entry["name"]
            for i, entry in enumerate(entries):
                if "name" in entry and entry["name"] == name_to_search:
                    return i
        return None

    def insert_entry(self, key: str, new_entry: Any) -> None:
        entries = self._entries_by_key(key)
        same_name_index = self._index_same_name(entries, new_entry)
        if same_name_index is None:
            entries.append(new_entry)
        else:
            entries[same_name_index] = new_entry

    def merge(self, new_config: Dict[str, Any]) -> None:
        for cluster in new_config.get("clusters", []):
            self.insert_entry("clusters", cluster)
        for user in new_config.get("users", []):
            self.insert_entry("users", user)
        for context in new_config.get("contexts", []):
            self.insert_entry("contexts", context)

        self.config["current-context"] = new_config["current-context"]

        for key in new_config.keys():
            if key not in ["clusters", "users", "contexts", "current-context"]:
                self.config[key] = new_config[key]


def update_kubeconfig() -> None:
    system_kubeconfig_path = os.path.expanduser("~/.kube/config")
    current_config = read_yaml_file(system_kubeconfig_path)
    merger = KubeconfigMerger(current_config)

    cluster_kubeconfig_path = os.path.join(
        get_project_data_dir(), "current_cluster", "kubeconfig.yaml"
    )
    new_config = read_yaml_file(cluster_kubeconfig_path)
    merger.merge(new_config)

    # Convert OrderedDict to dict and sort keys
    sorted_config = {k: merger.config[k] for k in sorted(merger.config)}

    # Dump the sorted config to a YAML-formatted string
    with open(system_kubeconfig_path, "w") as file:
        yaml = YAML()
        yaml.dump(sorted_config, file)


def tail_logs(namespace: str, pod_name: str) -> None:
    v1 = client.CoreV1Api()
    w = watch.Watch()

    while True:
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        if pod.status.phase == "Running":
            break
        elif pod.status.phase in ["Failed", "Succeeded"]:
            logger.info(f"\nPod {pod_name} is in phase {pod.status.phase}")
            return
        else:
            # print dot on the same line
            print(".", end="", flush=True)
            time.sleep(1)

    print("\n")
    for event in w.stream(
        v1.read_namespaced_pod_log, namespace=namespace, name=pod_name
    ):
        logger.info(event)


# Load the kubeconfig when this module is imported
try_load_kubeconfig()
