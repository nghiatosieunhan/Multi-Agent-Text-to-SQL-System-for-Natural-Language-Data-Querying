import sqlite3
c = sqlite3.connect('data/chinook/Chinook_VN.sqlite')
print(c.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall())
