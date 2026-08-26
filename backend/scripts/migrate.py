from sqlalchemy import text

from app.db import Base, engine


def main():
    db = engine()
    with db.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(db)


if __name__ == "__main__":
    main()

