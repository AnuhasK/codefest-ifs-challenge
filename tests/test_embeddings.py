from src.providers.embeddings import VoyageEmbeddingProvider, GeminiEmbeddingProvider, EmbeddingProvider


def test_embedding_provider_interface():
    assert issubclass(VoyageEmbeddingProvider, EmbeddingProvider)
    assert issubclass(GeminiEmbeddingProvider, EmbeddingProvider)


def test_voyage_provider_initialization():
    provider = VoyageEmbeddingProvider(api_key="mock-key-for-test", model="voyage-3-large")
    assert provider.dimension == 1024
    assert provider.model == "voyage-3-large"
