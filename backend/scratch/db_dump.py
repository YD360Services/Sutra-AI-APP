import sqlite3

def main():
    conn = sqlite3.connect('copilotx.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("Tables:", tables)
    for table_tuple in tables:
        table_name = table_tuple[0]
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"Table {table_name}: {count} rows")
        if count > 0:
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 3")
            rows = cursor.fetchall()
            print(f"Sample data from {table_name}:")
            for r in rows:
                print(r)
    conn.close()

if __name__ == '__main__':
    main()
