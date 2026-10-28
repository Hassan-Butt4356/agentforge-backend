"""
Direct retriever for cases where you want raw chunks without the ReActAgent.
Use this if you want to build a simpler Q&A endpoint without agentic reasoning.
"""
from llama_index.core.retrievers import VectorIndexRetriever
from rag.indexes.vector_index import load_index, configure_llama_settings


def get_retriever(agent_model, similarity_top_k: int = 4) -> VectorIndexRetriever:
    configure_llama_settings()
    index = load_index(agent_model.chroma_collection_name)
    return VectorIndexRetriever(index=index, similarity_top_k=similarity_top_k)
