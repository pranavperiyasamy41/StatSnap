import os
import bcrypt
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from database import get_db
import models

# Secret key to sign JWT tokens
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-for-portfolio-purposes")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

def get_password_hash(password: str):
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes[:72], salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str):
    try:
        pwd_bytes = plain_password.encode('utf-8')
        hashed_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(pwd_bytes[:72], hashed_bytes)
    except Exception:
        return False

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(request: Request, db: Session = Depends(get_db)):
    """
    Safely retrieves the current user. 
    Returns None instead of raising an error if the user is not logged in.
    """
    token = request.cookies.get("access_token")
    if not token:
        return None
    
    if token.startswith("Bearer "):
        token = token[7:]

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            return None
        
        return db.query(models.User).filter(models.User.email == email).first()
    except Exception:
        # Return None for any error (expired token, invalid format, etc.)
        return None
