"""Who answers a chat: OpenRouter, or the user's own llama.cpp server."""

OPENROUTER = "openrouter"
LLAMA_CPP = "llama.cpp"
PROVIDERS = (OPENROUTER, LLAMA_CPP)
PROVIDER_LABELS = {OPENROUTER: "OpenRouter", LLAMA_CPP: "Own llama.cpp server"}

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Sent when no llama.cpp model is set: a server with one model answers with it whatever the name.
LLAMA_CPP_DEFAULT_MODEL = "default"
