import os
from dotenv import load_dotenv

load_dotenv()
INDEX_ID=os.getenv('INDEX_ID')
MODEL_ID=os.getenv('MODEL_ID')
AWS_ACCESS_KEY_ID=os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY=os.getenv('AWS_SECRET_ACCESS_KEY')
REGION_NAME=os.getenv('AWS_DEFAULT_REGION')
AI_TEMPERATURE=os.getenv('AI_TEMPERATURE')
SYSTEM_MESSAGE=os.getenv('SYSTEM_MESSAGE')
AI_MAX_TOKENS=os.getenv('AI_MAX_TOKENS')
AI_TOP_P=os.getenv('AI_TOP_P')
AI_SEARCH_TOP_K = os.getenv('AI_SEARCH_TOP_K')