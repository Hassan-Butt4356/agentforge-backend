import logging

from llama_index.core.llms import ChatMessage, MessageRole

from rag.indexes.vector_index import load_index, configure_llama_settings

from rag.agents.base_agent import build_rag_agent

logger = logging.getLogger(__name__)


class ChatService:
    """
    Wraps the ReActAgent for multi-turn conversation.

    The agent is built lazily on first use (avoids loading ChromaDB on import).
    Each ChatService instance is scoped to one agent — one instance per request.

    In production with many concurrent users you'd cache the built agent
    (or the loaded VectorStoreIndex) in Redis/Django cache keyed by agent.id
    to avoid rebuilding it on every single request.
    """

    def __init__(self, agent_model):
        self.agent_model = agent_model
        self._agent = None

    def _load_agent(self):
        if self._agent is None:
            self._agent = build_rag_agent(self.agent_model)
        return self._agent

    def _build_chat_history(self, history: list[dict]) -> list[ChatMessage]:
        messages = []
        for msg in history:
            role = MessageRole.USER if msg["role"] == "user" else MessageRole.ASSISTANT
            messages.append(ChatMessage(role=role, content=msg["content"]))
        return messages

    def chat(self, user_message: str, history: list[dict]) -> str:
        try:
            agent = self._load_agent()
            chat_history = self._build_chat_history(history)
            response = agent.chat(user_message, chat_history=chat_history)
            answer = str(response).strip()
            if not answer:
                return "I could not find relevant information for your question."
            return answer

        except ValueError as e:
            # ReActAgent found the answer but hit max_iterations before finalising.
            # Extract the last response from the agent's memory.
            if "Reached max iterations" in str(e):
                try:
                    # Get the last assistant message from agent memory
                    last_response = agent._agent_worker._get_latest_tool_output()
                    if last_response:
                        return str(last_response).strip()
                except Exception:
                    pass
                # Fallback: re-run as a simple query engine instead
                configure_llama_settings()
                index = load_index(self.agent_model.chroma_collection_name)
                response = index.as_query_engine(similarity_top_k=6).query(user_message)
                return str(response).strip()

        except Exception as e:
            logger.error("ChatService error: %s", e, exc_info=True)
            return "I could not find relevant information for your question."
