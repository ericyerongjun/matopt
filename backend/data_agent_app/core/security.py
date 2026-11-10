import bcrypt


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt."""
    # bcrypt has a maximum password length of 72 bytes
    # We truncate the password to 72 bytes before hashing
    password_bytes = password.encode('utf-8')
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]
    
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')
