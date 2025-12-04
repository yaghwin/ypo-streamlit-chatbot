import streamlit as st
import pandas as pd
import re

st.title("❄️ What do you need help with?")

# --- Snowflake connection using Streamlit's connection management ---
# This automatically reconnects if the connection is closed
conn = st.connection("snowflake")

# Fetch table and column metadata
@st.cache_data(ttl=600)
def get_schema_info():
    """Fetch tables and their columns from the database."""
    tables_df = conn.query("""
        SELECT TABLE_NAME, TABLE_TYPE 
        FROM YPO.INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_SCHEMA = 'YPO_DATA'
        ORDER BY TABLE_TYPE, TABLE_NAME
    """)
    
    columns_df = conn.query("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM YPO.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'YPO_DATA'
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """)
    
    return tables_df, columns_df

try:
    tables_df, columns_df = get_schema_info()
    available_objects = tables_df['TABLE_NAME'].tolist() if not tables_df.empty else []
    
    # Build schema description for the LLM
    schema_description = ""
    for table in available_objects:
        table_cols = columns_df[columns_df['TABLE_NAME'] == table]
        cols_str = ", ".join([f"{row['COLUMN_NAME']} ({row['DATA_TYPE']})" for _, row in table_cols.iterrows()])
        schema_description += f"\n- {table}: {cols_str}"
    
except Exception as e:
    st.error(f"Could not fetch schema: {e}")
    available_objects = []
    schema_description = ""

# Show available tables/views in the schema
# with st.expander("📋 Available Tables & Views in YPO.YPO_DATA"):
#     if tables_df is not None and not tables_df.empty:
#         for table in available_objects:
#             st.markdown(f"**{table}**")
#             table_cols = columns_df[columns_df['TABLE_NAME'] == table][['COLUMN_NAME', 'DATA_TYPE']]
#             st.dataframe(table_cols, hide_index=True)
#     else:
#         st.warning("No tables or views found in YPO.YPO_DATA schema")

# Chat input
user_query = st.text_input("Ask me about your data:")

def extract_sql(response):
    """Extract SQL from LLM response - handles markdown code blocks or plain SQL."""
    # Try to find SQL in markdown code block
    sql_match = re.search(r"```(?:sql)?\s*(.*?)```", response, re.DOTALL | re.IGNORECASE)
    if sql_match:
        return sql_match.group(1).strip()
    
    # Try to find a SELECT statement directly
    select_match = re.search(r"(SELECT\s+.*?;?)\s*$", response, re.DOTALL | re.IGNORECASE)
    if select_match:
        return select_match.group(1).strip()
    
    # Return trimmed response as fallback
    return response.strip()

def generate_sql(nl_query, schema_desc):
    # Prompt Cortex to generate SQL against available tables
    # Escape single quotes in user query to prevent SQL injection
    escaped_query = nl_query.replace("'", "''")
    escaped_schema = schema_desc.replace("'", "''")
    prompt = (
        "You are a SQL generator. Output ONLY the SQL query, nothing else. "
        "No explanations, no markdown, no comments - just the raw SQL. "
        f"Database schema (table: columns):{escaped_schema}\\n\\n"
        "Use fully qualified names like YPO.YPO_DATA.TABLE_NAME. "
        "Use the EXACT column names shown above. "
        "Only SELECT queries allowed.\\n\\nRequest: " + escaped_query
    )
    result = conn.query(f"""
        SELECT SNOWFLAKE.CORTEX.COMPLETE('mistral-large2', '{prompt}') AS generated_sql
    """)
    raw_response = result.iloc[0]['GENERATED_SQL']
    return extract_sql(raw_response)

if user_query:
    try:
        sql_text = generate_sql(user_query, schema_description)
        st.write("🔎 Generated SQL")
        st.code(sql_text, language="sql")

        # Safety check: block DML/DDL
        upper_sql = sql_text.upper()
        forbidden = any(x in upper_sql for x in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE "])
        if forbidden:
            st.error("Generated SQL contains write operations. Aborting.")
        else:
            df = conn.query(sql_text)

            st.write("📊 Results")
            st.dataframe(df)

            # Auto‑chart if numeric data is present
            num_cols = df.select_dtypes(include=["int64", "float64"]).columns
            if len(num_cols) > 0:
                st.write("📈 Visualization")
                if len(num_cols) == 1:
                    st.bar_chart(df[num_cols[0]])
                else:
                    st.line_chart(df[num_cols])

    except Exception as e:
        st.error(f"Error: {e}")
