param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"

$Python = "python"
if (Test-Path ".\.venv\Scripts\python.exe") {
    $Python = ".\.venv\Scripts\python.exe"
}

Write-Host "Running targeted Agent stream tests..."
& $Python -m pytest `
    tests/test_agent_console_ux.py::test_tool_lifecycle_events_include_target_node_id `
    tests/test_agent_console_ux.py::test_prompt_context_lists_source_nodes_for_duplicate_capabilities `
    tests/test_agent_console_ux.py::test_available_functions_filters_to_pinned_node_in_production_mode `
    tests/test_agent_console_ux.py::test_no_online_node_produces_explicit_diagnostic `
    tests/test_agent_parallel_tools.py::test_four_safe_read_tools_concurrent_created_events
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if (-not $SkipFrontend) {
    Write-Host "Running frontend typecheck..."
    Push-Location console-frontend
    try {
        npm run typecheck
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    finally {
        Pop-Location
    }
}
