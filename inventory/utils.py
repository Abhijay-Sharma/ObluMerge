import pyodbc

def fetch_tally_stock():
    conn_str = (
        r"DSN=TallyODBC64_9000;"
    )
    try:
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()

        query = "SELECT $Name, $ClosingBalance, $BaseUnits FROM StockItem"
        cursor.execute(query)

        result = {}
        for row in cursor.fetchall():
            name = row[0] if row[0] else ''
            balance = row[1]
            unit = row[2]
            result[name] = {
                "balance": balance,
                "unit": unit
            }
        return result
    except Exception as e:
        print("Error:", e)
        return []
