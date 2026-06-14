import logging

from llama_index.core.agent import ReActAgent
from llama_index.core.tools import QueryEngineTool, ToolMetadata

from rag.indexes.vector_index import load_index, configure_llama_settings

logger = logging.getLogger(__name__)


def build_rag_agent(agent_model) -> ReActAgent:
    """
    Build a LlamaIndex ReActAgent for a given Django Agent.

    The agent is given one tool: a QueryEngineTool backed by the
    agent's ChromaDB collection. The ReAct loop lets the LLM decide
    when to call the tool versus answer from its own knowledge.

    This function is called inside ChatService on the first chat request.
    It's kept separate from ChatService so more tools can be added here
    without touching the service layer.

    Args:
        agent_model: Django Agent instance (needs chroma_collection_name).

    Returns:
        Configured ReActAgent ready to receive .chat() calls.
    """
    configure_llama_settings()

    index = load_index(agent_model.chroma_collection_name)

    query_engine = index.as_query_engine(
        similarity_top_k=4,    # retrieve top 4 most relevant chunks per query
        streaming=False,
    )

    rag_tool = QueryEngineTool(
        query_engine=query_engine,
        metadata=ToolMetadata(
            name="document_search",
            description=(
                f"Search the '{agent_model.name}' knowledge base. "
                f"Use this for any question about the content of the uploaded documents. "
                f"Input: a natural language question."
            ),
        ),
    )

    react_agent = ReActAgent.from_tools(
        tools=[rag_tool],
        verbose=True,
        max_iterations=10,
    )

    logger.info("Built ReActAgent for agent '%s'", agent_model.name)
    return react_agent
