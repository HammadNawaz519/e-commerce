import mysql.connector

conn = mysql.connector.connect(
    host='localhost',
    user='root',
    password='Hamm123..'
)
cur = conn.cursor()

# Create the shopy database
cur.execute('CREATE DATABASE IF NOT EXISTS shopy CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
print('Database "shopy" created / already exists.')

cur.execute('USE shopy')

# Show existing tables
cur.execute('SHOW TABLES')
tables = [t[0] for t in cur.fetchall()]
print('Existing tables:', tables)

cur.close()
conn.close()
print('Done.')
