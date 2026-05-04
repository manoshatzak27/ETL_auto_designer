"""One-time migration: add any missing columns to the projects table."""
from sqlalchemy import inspect, text
from app.database import engine

with engine.connect() as conn:
    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns("projects")}

    needed = {
        "concept_decisions": "ALTER TABLE projects ADD COLUMN concept_decisions JSON",
        "mapping_files": "ALTER TABLE projects ADD COLUMN mapping_files JSON",
        "generated_scripts": "ALTER TABLE projects ADD COLUMN generated_scripts JSON",
        "chat_history": "ALTER TABLE projects ADD COLUMN chat_history JSON",
    }

    for col, ddl in needed.items():
        if col not in existing:
            conn.execute(text(ddl))
            print(f"Added column: {col}")
        else:
            print(f"Already exists: {col}")

    conn.commit()

print("Migration complete.")
