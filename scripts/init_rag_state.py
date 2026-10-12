"""Create only the additive RAG catalog/memory tables using local .env credentials."""
from pathlib import Path
import sys
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / '.env')
from state_store import StateStore, schema

if __name__ == '__main__':
    state = StateStore()
    print('RAG tables ready: ' + ', '.join(schema.tables))
    print('Session persistence: MySQL; Redis: ' + state.cache_status)
