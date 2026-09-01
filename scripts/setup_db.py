import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database.postgres import init_postgres
from src.database.neo4j_db import init_neo4j


def main():
    print("Initializing databases...")
    try:
        init_postgres()
    except Exception as e:
        print(f"Error initializing PostgreSQL: {e}")
        sys.exit(1)

    try:
        init_neo4j()
    except Exception as e:
        print(f"Error initializing Neo4j constraints: {e}")
        print("Note: If Neo4j is starting up, please wait a few seconds and rerun.")
        sys.exit(1)

    print("All databases successfully initialized and schemas verified.")


if __name__ == "__main__":
    main()
