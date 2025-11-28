from database import SessionLocal, Stock

def clean_db():
    db = SessionLocal()
    try:
        # Find stocks with None or empty symbol
        bad_stocks = db.query(Stock).filter((Stock.symbol == None) | (Stock.symbol == "")).all()
        print(f"Found {len(bad_stocks)} bad stocks.")
        
        for s in bad_stocks:
            print(f"Deleting stock with ID {s.id} and symbol '{s.symbol}'")
            db.delete(s)
        
        if bad_stocks:
            db.commit()
            print("Deleted bad stocks.")
        else:
            print("No bad stocks found.")
            
    finally:
        db.close()

if __name__ == "__main__":
    clean_db()
