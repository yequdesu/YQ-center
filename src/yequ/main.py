"""YeQu Center entry point."""

import uvicorn


def main() -> None:
    """Start the YeQu Center server."""
    uvicorn.run(
        "yequ.api.app:create_app",
        host="0.0.0.0",
        port=9800,
        factory=True,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
