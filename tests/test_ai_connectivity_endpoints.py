from scripts.ai_connectivity_test import AIConnectivityTester


def test_chat_endpoint_normalization_for_custom_openai_compatible_base_urls():
    assert (
        AIConnectivityTester._normalize_chat_endpoint(
            "OTHER", "https://example.invalid/v1"
        )
        == "https://example.invalid/v1/chat/completions"
    )
    assert (
        AIConnectivityTester._normalize_chat_endpoint(
            "OTHER", "https://example.invalid/v1/chat/completions/"
        )
        == "https://example.invalid/v1/chat/completions"
    )


def test_models_endpoint_builder_does_not_duplicate_v1():
    assert (
        AIConnectivityTester._build_models_endpoint("https://example.invalid/v1")
        == "https://example.invalid/v1/models"
    )
    assert (
        AIConnectivityTester._build_models_endpoint(
            "https://example.invalid/v1/chat/completions"
        )
        == "https://example.invalid/v1/models"
    )
    assert (
        AIConnectivityTester._build_models_endpoint("https://example.invalid")
        == "https://example.invalid/v1/models"
    )
