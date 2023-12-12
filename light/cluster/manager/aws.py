from typing import Any, Dict
from light.cluster.manager.config import Config, CloudConfig
from light.cluster.iac.aws.iam import iam_user
from pulumi import automation as auto
from functools import partial


class AWSClusterManager:
    config: CloudConfig

    def __init__(self, config: Config) -> None:
        if config.aws is None:
            raise ValueError("AWS config is required")
        self.config = config.aws

    def provision_iam_user(self) -> None:
        iam_user(self.config.cluster.name)

    def create(self) -> None:
        project_name = self.config.cluster.name
        stack_name = "prod"
        pulumi_program = partial(self.provision_iam_user)

        stack = auto.create_or_select_stack(
            stack_name=stack_name, project_name=project_name, program=pulumi_program
        )

        # Set AWS region
        stack.set_config(
            "aws:region", auto.ConfigValue(value=self.config.cluster.defaultRegion)
        )

        # Deploy the stack
        print("Creating resources...")
        stack.up(on_output=print)

    def destroy(self) -> None:
        project_name = self.config.cluster.name
        stack_name = "prod"

        pulumi_program = partial(self.provision_iam_user)

        # Select the stack
        stack = auto.select_stack(
            stack_name=stack_name, project_name=project_name, program=pulumi_program
        )

        # Destroy the stack resources
        print("Destroying resources...")
        stack.destroy(on_output=print)
