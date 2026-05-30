import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent.parent))

from memory.config import HubConfig
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent
from memory.interfaces.mcp_server import create_mcp_server

def main():
    # Use a specific configuration for tests to ensure they don't mess with real data
    # and use the requested embedding model.
    config = HubConfig()
    
    config.openai.base_url = "http://localhost:11434/v1"
    config.openai.api_key = "ollama" # dummy key
    
    # Use in-memory stores for isolated testing
    config.providers.vector_db = "in_memory"
    config.providers.metadata_db = "sqlite"
    # For sqlite in-memory, we'd need to change how SQLiteMetadataStore handles paths, 
    # but for now we'll just use a temp file or the default data dir if we don't mind.
    # Actually, let's use a temporary data directory.
    import tempfile
    temp_dir = tempfile.mkdtemp()
    config.paths.data_dir = temp_dir
    
    agent = MVPIngestionAgent(config=config)
    mcp = create_mcp_server(config=config, agent=agent)
    
    # Run the MCP server
    mcp.run()

if __name__ == "__main__":
    main()
