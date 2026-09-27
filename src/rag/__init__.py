"""RAG (Retrieval-Augmented Generation) Directory

This directory contains RAG pipeline components for the JamBit OS system.
RAG systems enhance AI responses by retrieving relevant context from cached data.
"""

from .discord_rag import DiscordRAG, initialize_discord_rag

__all__ = ['DiscordRAG', 'initialize_discord_rag']
