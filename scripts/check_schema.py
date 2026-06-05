import sqlite3
c = sqlite3.connect('data/chinook/Chinook_VN.sqlite')
for row in c.execute("SELECT name, sql FROM sqlite_master WHERE type='table';").fetchall():
    if row[0] in ['Album', 'NgheSi', 'BaiHat']:
        print(row[1])
