import pyodbc, os
from dotenv import load_dotenv

load_dotenv()

username = os.getenv('USERNAME_DB')
password = os.getenv('PASSWORD_DB')
server = os.getenv('SERVER')  
database = os.getenv('DATABASE')  
dsn = os.getenv('DSN')  


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


