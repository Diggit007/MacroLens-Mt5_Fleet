
import chromadb
from chromadb.config import Settings
import logging
from typing import List, Dict, Optional
import uuid
from datetime import datetime
from pathlib import Path

# Setup Logger
logger = logging.getLogger("MemoryService")

class MemoryStore:
    def __init__(self, persist_path: str = "./backend/chroma_db"):
        """
        Initialize the ChromaDB Persistent Client.
        """
        try:
            # Check if path exists or let Chroma create it
            # Resolving absolute path to avoid confusion
            self.persist_path = str(Path(persist_path).resolve())
            
            logger.info(f"Initializing Memory Store at {self.persist_path}")
            
            self.client = chromadb.PersistentClient(path=self.persist_path)
            
            # Create or Get the collection
            self.collection = self.client.get_or_create_collection(
                name="macrolens_agent_memory",
                metadata={"hnsw:space": "cosine"} # Cosine similarity for semantic search
            )
            logger.info("Memory Store initialized successfully.")
            
        except Exception as e:
            logger.error(f"Failed to initialize Memory Store: {e}")
            self.client = None
            self.collection = None

    def add_memory(self, text: str, meta: Dict = None) -> bool:
        """
        Store a text memory with metadata.
        """
        if not self.collection:
            return False
            
        try:
            memory_id = str(uuid.uuid4())
            timestamp = datetime.utcnow().isoformat()
            
            # Default metadata
            final_meta = {
                "timestamp": timestamp,
                "type": "general"
            }
            if meta:
                final_meta.update(meta)
                
            self.collection.add(
                documents=[text],
                metadatas=[final_meta],
                ids=[memory_id]
            )
            return True
        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            return False

    def recall(self, query: str, n_results: int = 3, filter_meta: Dict = None) -> List[Dict]:
        """
        Retrieve semantically similar memories.
        """
        if not self.collection:
            return []
            
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where=filter_meta # Optional filtering
            )
            
            # Reformat results
            memories = []
            if results['documents']:
                for i, doc in enumerate(results['documents'][0]):
                    meta = results['metadatas'][0][i]
                    # dist = results['distances'][0][i] # Similarity score
                    memories.append({
                        "text": doc,
                        "metadata": meta
                    })
            
            return memories
            
        except Exception as e:
            logger.error(f"Failed to recall memory: {e}")
            return []

    def verify_health(self) -> bool:
        return self.client is not None

# Global Instance
memory_store = MemoryStore()
