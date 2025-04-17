import sqlite3

# Connect to SQLite database (creates it if it doesn't exist)
conn = sqlite3.connect("functions.db")
c = conn.cursor()

# Create the `functions` table
c.execute('''
    CREATE TABLE IF NOT EXISTS functions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        route TEXT NOT NULL,
        language TEXT NOT NULL,
        timeout INTEGER NOT NULL
    )
''')

# Commit and close connection
conn.commit()
conn.close()

print("Database and table created successfully!")
