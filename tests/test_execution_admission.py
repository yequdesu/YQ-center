from __future__ import annotations

from yequ.application.schemas import ExecuteToolCommand
from yequ.runtime.admission import ExecutionAdmissionService


def test_execution_admission_meta_tools_inline() -> None:
    service = ExecutionAdmissionService()

    for function_name in (
        "node.list",
        "capability.search",
        "artifact.present",
        "artifact.deploy.preflight",
        "operation.status",
        "transfer.preflight",
    ):
        plan = service.plan(ExecuteToolCommand(function_name=function_name))

        assert plan.decision == "inline"
        assert not plan.waitable


def test_execution_admission_transfer_create_workflow_operation() -> None:
    plan = ExecutionAdmissionService().plan(
        ExecuteToolCommand(
            function_name="transfer.create",
            input_data={
                "source_node_id": "winClient",
                "target_node_id": "linux-node-01",
                "source_path": "C:\\data\\big.iso",
                "target_output_dir": "/tmp/yequ-transfer",
            },
        )
    )

    assert plan.decision == "workflow_operation"
    assert plan.operation_kind == "transfer"
    assert plan.waitable


def test_execution_admission_artifact_deploy_waitable_job_operation() -> None:
    plan = ExecutionAdmissionService().plan(
        ExecuteToolCommand(
            function_name="artifact.deploy",
            input_data={
                "artifact_id": "id_test",
                "target_node_id": "linux-node-01",
                "output_path": "/tmp/yequ-transfer/a.bin",
            },
        )
    )

    assert plan.decision == "waitable_operation"
    assert plan.operation_kind == "job"
    assert plan.waitable


def test_execution_admission_does_not_use_prompt_text() -> None:
    command = ExecuteToolCommand(
        function_name="transfer.create",
        input_data={
            "source_node_id": "winClient",
            "target_node_id": "linux-node-01",
            "source_path": "C:\\small.txt",
            "target_output_dir": "/tmp/yequ-transfer",
            "prompt": "please do this synchronously",
        },
    )

    plan = ExecutionAdmissionService().plan(command)

    assert plan.decision == "workflow_operation"
