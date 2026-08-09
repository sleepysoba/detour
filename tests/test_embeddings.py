import pytest

from detour.embeddings import EmbeddingError, EmbeddingService, vector_literal


class FakeModel:
    def __init__(self, dimensions=384):
        self.dimensions = dimensions

    def get_sentence_embedding_dimension(self):
        return self.dimensions

    def encode(self, texts, **kwargs):
        return [[float(index == 0) for index in range(self.dimensions)] for _ in texts]


def test_embedding_service_supports_single_and_batch_inputs():
    service = EmbeddingService(model_loader=lambda _: FakeModel())

    assert len(service.embed("one")) == 384
    assert [len(vector) for vector in service.embed_batch(["one", "two"])] == [384, 384]


def test_embedding_service_rejects_wrong_model_dimension():
    service = EmbeddingService(model_loader=lambda _: FakeModel(768))

    with pytest.raises(EmbeddingError, match="expected 384"):
        service.embed("one")


def test_vector_literal_validates_dimensions():
    assert vector_literal([0.0] * 384).startswith("[")
    with pytest.raises(EmbeddingError, match="expected 384"):
        vector_literal([0.0] * 3)
