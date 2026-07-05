import sys
import types

import pytest


@pytest.mark.asyncio
async def test_ycr_embedder_loads_embedding_model_and_reranker(monkeypatch):
    from yequ import ycr_embedder_app

    created: dict[str, object] = {}

    class FakeBgeModel:
        def __init__(self, model_name, *, use_fp16, device):
            created["model"] = {
                "model_name": model_name,
                "use_fp16": use_fp16,
                "device": device,
            }

    class FakeReranker:
        def __init__(self, model_name, *, use_fp16):
            created["reranker"] = {"model_name": model_name, "use_fp16": use_fp16}

    fake_module = types.SimpleNamespace(
        BGEM3FlagModel=FakeBgeModel,
        FlagReranker=FakeReranker,
    )
    monkeypatch.setitem(sys.modules, "FlagEmbedding", fake_module)
    monkeypatch.setattr(ycr_embedder_app, "_model", None)
    monkeypatch.setattr(ycr_embedder_app, "_reranker", None)
    monkeypatch.setenv("YEQU_EMBEDDER_MODEL", "local-bge")
    monkeypatch.setenv("YEQU_YCR_RERANK_MODEL", "local-reranker")
    monkeypatch.setenv("YEQU_EMBEDDER_DEVICE", "cpu")
    monkeypatch.setenv("YEQU_EMBEDDER_USE_FP16", "false")

    model = await ycr_embedder_app._load_model()
    reranker = await ycr_embedder_app._load_reranker()

    assert isinstance(model, FakeBgeModel)
    assert isinstance(reranker, FakeReranker)
    assert created["model"] == {
        "model_name": "local-bge",
        "use_fp16": False,
        "device": "cpu",
    }
    assert created["reranker"] == {"model_name": "local-reranker", "use_fp16": False}
