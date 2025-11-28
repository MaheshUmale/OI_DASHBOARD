# OI Dashboard

This project is a Python-based web application that serves as an Open Interest (OI) dashboard for NSE F&O stocks. It allows users to view and analyze the Open Interest data for various stocks and indices to gauge market sentiment. The application fetches data from the NSE website, processes it, and stores it in a SQLite database for analysis.

## Project Structure

The project is organized into the following modules:

-   `oi_dashboard/app.py`: The main Flask application that handles routing, data fetching, and rendering of the dashboard.
-   `oi_dashboard/data_fetcher.py`: Responsible for fetching and processing the Open Interest data from the NSE API.
-   `oi_dashboard/database.py`: Defines the database schema using SQLAlchemy and provides a session for database interactions.
-   `oi_dashboard/seed.py`: A utility script to seed the database with an initial list of stock symbols.
-   `oi_data.db`: The SQLite database file where the OI data is stored.

## Application Logic

The application follows a simple yet effective logic to provide insights into the market sentiment:

1.  **Data Fetching**: The `data_fetcher.py` module fetches the Open Interest data from the NSE's public API for a given stock symbol. It can handle both indices (like NIFTY and BANKNIFTY) and individual stocks.

2.  **Data Processing**: After fetching the data, the application processes it to calculate key metrics, including:
    -   Total Call and Put OI
    -   Change in Call and Put OI
    -   OI Interpretation (e.g., Long Buildup, Short Buildup)

3.  **Data Storage**: The processed data is then stored in a SQLite database. The database schema is defined in `database.py` and consists of two tables:
    -   `stocks`: Stores the list of stock symbols.
    -   `oi_data`: Stores the time-series OI data for each stock.

4.  **Dashboard Display**: The Flask application in `app.py` renders the dashboard, which displays the OI data in a tabular format. Users can select a stock from a dropdown menu to view its corresponding data. The dashboard automatically refreshes every 60 seconds to provide the latest data.

## Getting Started

To run the application, follow these steps:

1.  **Install Dependencies**: Make sure you have Python and Pip installed. Then, install the required dependencies:
    ```bash
    pip install Flask requests SQLAlchemy
    ```

2.  **Initialize the Database**: Run the `database.py` script to create the database and tables:
    ```bash
    python oi_dashboard/database.py
    ```

3.  **Seed the Database**: Run the `seed.py` script to populate the database with an initial list of stocks:
    ```bash
    python oi_dashboard/seed.py
    ```

4.  **Run the Application**: Start the Flask application by running:
    ```bash
    python oi_dashboard/app.py
    ```

The application will be available at `http://127.0.0.1:5000` in your web browser.

## Database Schema

The database consists of two tables:

### `stocks`

| Column | Type    | Description                  |
| ------ | ------- | ---------------------------- |
| `id`   | Integer | Primary Key                  |
| `symbol` | String  | The stock symbol (e.g., "NIFTY") |

### `oi_data`

| Column                | Type    | Description                               |
| --------------------- | ------- | ----------------------------------------- |
| `id`                  | Integer | Primary Key                               |
| `stock_id`            | Integer | Foreign Key to the `stocks` table         |
| `date`                | Date    | The date of the data record               |
| `timestamp`           | String  | The time of the data record               |
| `ltp`                 | Float   | The Last Traded Price of the underlying   |
| `change_in_ltp`       | Float   | The change in the Last Traded Price       |
| `volume`              | Integer | The total traded volume                   |
| `future_oi`           | Integer | The Open Interest of the futures contract |
| `change_in_future_oi` | Integer | The change in the futures OI              |
| `call_oi`             | Integer | The total Call Open Interest              |
| `change_in_call_oi`   | Integer | The change in the Call OI                 |
| `put_oi`              | Integer | The total Put Open Interest               |
| `change_in_put_oi`    | Integer | The change in the Put OI                  |
| `oi_interpretation`   | String  | The interpretation of the OI data         |
| `buy_sell_signal`     | String  | A buy/sell signal based on the OI data    |
