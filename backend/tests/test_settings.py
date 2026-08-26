from app.settings import Settings


def test_settings_repr_never_contains_secrets():
    settings = Settings(
        deepseek_api_key="deepseek-secret-value",
        dashscope_api_key="dashscope-secret-value",
    )
    rendered = repr(settings)
    assert "deepseek-secret-value" not in rendered
    assert "dashscope-secret-value" not in rendered
    assert settings.dashscope_embedding_dimensions == 1024

