import pyodbc
from dotenv import load_dotenv
import os

load_dotenv()

username = os.getenv('USERNAME_DB')
password = os.getenv('PASSWORD_DB')  # Codifica a senha com caracteres especiais
server = os.getenv('SERVER')  # Alterado para usar a porta
database = os.getenv('DATABASE')
dsn = os.getenv('DSN')


def connect_to_database():
    connection_string = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};DATABASE={database};UID={username};PWD={password};"
        "Encrypt=no;TrustServerCertificate=yes;"
    )


    connection = pyodbc.connect(connection_string)

    print("Banco de dados conectado com sucesso!")
    return connection


