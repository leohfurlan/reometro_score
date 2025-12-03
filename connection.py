import pyodbc


username = 'qualidade'
password = 'qua@2019'  # Codifica a senha com caracteres especiais
server = 'SERVER01\\SQLEXPRESS'  # Alterado para usar a porta
database = 'teamsolutions'
dsn = "ODBC Driver 18 for SQL Server"


def connect_to_database():
    connection_string = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};DATABASE={database};UID={username};PWD={password};"
        "Encrypt=no;TrustServerCertificate=yes;"
    )
    #connection = pyodbc.connect(connection_string)


    #query = "SELECT * FROM dbo_ENSAIO"
    #engine = create_engine(f"mssql+pyodbc://{username}:%s@{server}/{database}?TrustServerCertificate=yes&driver={dsn}" % password)

    connection = pyodbc.connect(connection_string)

    print("Banco de dados conectado com sucesso!")
    return connection


