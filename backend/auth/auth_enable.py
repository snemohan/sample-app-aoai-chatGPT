import base64
from dotenv import load_dotenv
import jwt
import os
import json
from functools import wraps
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from quart import request, jsonify
from urllib.request import urlopen

load_dotenv(dotenv_path=".env")

# Azure AD Auth Settings
AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID")
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID")


# Azure AD Authentication Logic
class AuthError(Exception):
    def __init__(self, error, status_code):
        self.error = error
        self.status_code = status_code

def ensure_bytes(key):
    if isinstance(key, str):
        key = key.encode('utf-8')
    return key

def decode_value(val):
    decoded = base64.urlsafe_b64decode(ensure_bytes(val) + b'==')
    return int.from_bytes(decoded, 'big')


def rsa_pem_from_jwk(jwk):
    return RSAPublicNumbers(
        n=decode_value(jwk['n']),
        e=decode_value(jwk['e'])
    ).public_key(default_backend()).public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )


def fetch_jwks():
    jwks_url = "https://login.microsoftonline.com/" + AZURE_TENANT_ID + "/discovery/v2.0/keys"
    jwks_response = urlopen(jwks_url)
    jwks_data = json.loads(jwks_response.read())
    return jwks_data


def decode_jwt_token(token):
    jwks = fetch_jwks()

    unverified_header = jwt.get_unverified_header(token)
    key_id = unverified_header.get('kid')

    jwk = next((key for key in jwks["keys"] if key["kid"] == key_id), None)

    if jwk is None:
        raise AuthError({"code": "invalid_header", "description": "Unable to find appropriate key"}, 401)

    public_key = rsa_pem_from_jwk(jwk)

    try:
        payload = jwt.decode(
            token,
            public_key,
            verify=True,
            algorithms=["RS256"],
            audience=AZURE_CLIENT_ID,
            issuer="https://login.microsoftonline.com/" + AZURE_TENANT_ID + "/v2.0"
        )
        return payload
    except jwt.ExpiredSignatureError as es:
        raise AuthError({"code": "invalid Signature or Token", "description": f"Invalid Signature: {es}"}, 401)
    except (jwt.InvalidAudienceError, jwt.InvalidIssuerError) as e:
        raise AuthError({"code": "invalid Audience or Issuer", "description": f"{e}"}, 401)
    except Exception as ex:
        raise AuthError({"code": "invalid_header", "description": f"Unable to parse authentication token: {ex}"}, 401)


def requires_auth(roles=[]):
    def decorator(f):
        @wraps(f)
        async def wrapper(*args, **kwargs):
            decoded_token = None

            try:
                authorization_header = request.headers.get("Authorization")
                if not authorization_header or not authorization_header.startswith("Bearer"):
                    raise AuthError({"code": "invalid_header", "description": "No valid Authorization header found"}, 401)

                token = authorization_header[len('Bearer '):]
                decoded_token = decode_jwt_token(token)

                if roles:
                    if not any(role in decoded_token.get('roles', []) for role in roles):
                        raise AuthError({"code": "insufficient_roles", "description": "Insufficient roles"}, 403)

            except AuthError as e:
                return jsonify({'error': e.error}), e.status_code

            if 'decoded_token' in kwargs:
                kwargs['decoded_token'] = decoded_token

            return await f(*args, **kwargs)

        return wrapper

    return decorator
