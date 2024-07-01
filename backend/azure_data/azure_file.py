import copy
import json
import os
import logging
# import uuid
from dotenv import load_dotenv
import httpx
from quart import (
    Blueprint,
    # Quart,
    jsonify,
    make_response,
    request,
    # send_from_directory,
    # render_template,
)
from openai import AsyncAzureOpenAI
from azure.identity.aio import (
    DefaultAzureCredential,
    get_bearer_token_provider
    )
# from backend.auth.auth_utils import get_authenticated_user_details

# from backend.history.cosmosdbservice import CosmosConversationClient

from backend.utils import (
    format_as_ndjson,
    format_stream_response,
    generateFilterString,
    parse_multi_columns,
    format_non_streaming_response,
    convert_to_pf_format,
    format_pf_non_streaming_response,
)

from .constants import (
    # AZURE_COSMOSDB_ACCOUNT,
    # AZURE_COSMOSDB_ACCOUNT_KEY,
    # AZURE_COSMOSDB_CONVERSATIONS_CONTAINER,
    # AZURE_COSMOSDB_DATABASE,
    # AZURE_COSMOSDB_ENABLE_FEEDBACK,
    AZURE_OPENAI_EMBEDDING_ENDPOINT,
    AZURE_OPENAI_EMBEDDING_KEY,
    AZURE_OPENAI_EMBEDDING_NAME,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_KEY,
    AZURE_OPENAI_MAX_TOKENS,
    AZURE_OPENAI_MODEL,
    AZURE_OPENAI_RESOURCE,
    AZURE_OPENAI_STOP_SEQUENCE,
    # AZURE_OPENAI_SYSTEM_MESSAGE,
    SYSTEM_MESSAGE,
    AZURE_OPENAI_TEMPERATURE,
    AZURE_OPENAI_TOP_P,
    AZURE_SEARCH_CONTENT_COLUMNS,
    AZURE_SEARCH_ENABLE_IN_DOMAIN,
    AZURE_SEARCH_FILENAME_COLUMN,
    AZURE_SEARCH_KEY,
    AZURE_SEARCH_PERMITTED_GROUPS_COLUMN,
    AZURE_SEARCH_QUERY_TYPE,
    AZURE_SEARCH_SEMANTIC_SEARCH_CONFIG,
    AZURE_SEARCH_SERVICE,
    AZURE_SEARCH_INDEX,
    AZURE_OPENAI_PREVIEW_API_VERSION,
    AZURE_SEARCH_STRICTNESS,
    AZURE_SEARCH_TITLE_COLUMN,
    AZURE_SEARCH_TOP_K,
    AZURE_SEARCH_URL_COLUMN,
    AZURE_SEARCH_USE_SEMANTIC_SEARCH,
    AZURE_SEARCH_VECTOR_COLUMNS,
    CHAT_HISTORY_ENABLED,
    MINIMUM_SUPPORTED_AZURE_OPENAI_PREVIEW_API_VERSION,
    PROMPTFLOW_API_KEY,
    PROMPTFLOW_CITATIONS_FIELD_NAME,
    PROMPTFLOW_ENDPOINT,
    PROMPTFLOW_REQUEST_FIELD_NAME,
    PROMPTFLOW_RESPONSE_FIELD_NAME,
    PROMPTFLOW_RESPONSE_TIMEOUT,
    SEARCH_STRICTNESS,
    SEARCH_TOP_K,
    SHOULD_STREAM,
    USE_PROMPTFLOW,
    USER_AGENT
)

bp = Blueprint("routes", __name__)

# # Current minimum Azure OpenAI version supported
# MINIMUM_SUPPORTED_AZURE_OPENAI_PREVIEW_API_VERSION = "2024-02-15-preview"

load_dotenv()

# # Debug settings
# DEBUG = os.environ.get("DEBUG", "false")
# if DEBUG.lower() == "true":
#     logging.basicConfig(level=logging.DEBUG)

def should_use_data():
    global DATASOURCE_TYPE
    if AZURE_SEARCH_SERVICE and AZURE_SEARCH_INDEX:
        DATASOURCE_TYPE = "AzureCognitiveSearch"
        logging.debug("Using Azure Cognitive Search")
        return True
    return False


SHOULD_USE_DATA = should_use_data()

# Initialize Azure OpenAI Client
def init_openai_client(use_data=SHOULD_USE_DATA):
    azure_openai_client = None
    try:
        # API version check
        if (
            AZURE_OPENAI_PREVIEW_API_VERSION
            < MINIMUM_SUPPORTED_AZURE_OPENAI_PREVIEW_API_VERSION
        ):
            raise Exception(
                f"The minimum supported Azure OpenAI preview API version is '{MINIMUM_SUPPORTED_AZURE_OPENAI_PREVIEW_API_VERSION}'"
            )

        # Endpoint
        if not AZURE_OPENAI_ENDPOINT and not AZURE_OPENAI_RESOURCE:
            raise Exception(
                "AZURE_OPENAI_ENDPOINT or AZURE_OPENAI_RESOURCE is required"
            )

        endpoint = (
            AZURE_OPENAI_ENDPOINT
            if AZURE_OPENAI_ENDPOINT
            else f"https://{AZURE_OPENAI_RESOURCE}.openai.azure.com/"
        )

        # Authentication
        aoai_api_key = AZURE_OPENAI_KEY
        ad_token_provider = None
        if not aoai_api_key:
            logging.debug("No AZURE_OPENAI_KEY found, using Azure AD auth")
            ad_token_provider = get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            )

        # Deployment
        deployment = AZURE_OPENAI_MODEL
        if not deployment:
            raise Exception("AZURE_OPENAI_MODEL is required")

        # Default Headers
        default_headers = {"x-ms-useragent": USER_AGENT}

        azure_openai_client = AsyncAzureOpenAI(
            api_version=AZURE_OPENAI_PREVIEW_API_VERSION,
            api_key=aoai_api_key,
            azure_ad_token_provider=ad_token_provider,
            default_headers=default_headers,
            azure_endpoint=endpoint,
        )

        return azure_openai_client
    except Exception as e:
        logging.exception("Exception in Azure OpenAI initialization", e)
        azure_openai_client = None
        raise e


# def init_cosmosdb_client():
#     cosmos_conversation_client = None
#     if CHAT_HISTORY_ENABLED:
#         try:
#             cosmos_endpoint = (
#                 f"https://{AZURE_COSMOSDB_ACCOUNT}.documents.azure.com:443/"
#             )

#             if not AZURE_COSMOSDB_ACCOUNT_KEY:
#                 credential = DefaultAzureCredential()
#             else:
#                 credential = AZURE_COSMOSDB_ACCOUNT_KEY

#             cosmos_conversation_client = CosmosConversationClient(
#                 cosmosdb_endpoint=cosmos_endpoint,
#                 credential=credential,
#                 database_name=AZURE_COSMOSDB_DATABASE,
#                 container_name=AZURE_COSMOSDB_CONVERSATIONS_CONTAINER,
#                 enable_message_feedback=AZURE_COSMOSDB_ENABLE_FEEDBACK,
#             )
#         except Exception as e:
#             logging.exception("Exception in CosmosDB initialization", e)
#             cosmos_conversation_client = None
#             raise e
#     else:
#         logging.debug("CosmosDB not configured")

#     return cosmos_conversation_client


def get_configured_data_source(application_id, run_id):
    data_source = {}
    query_type = AZURE_SEARCH_QUERY_TYPE  #"simple"
    if DATASOURCE_TYPE == "AzureCognitiveSearch":
        # Set query type
        if AZURE_SEARCH_QUERY_TYPE:
            query_type = AZURE_SEARCH_QUERY_TYPE
        elif (
            AZURE_SEARCH_USE_SEMANTIC_SEARCH.lower() == "true"
            and AZURE_SEARCH_SEMANTIC_SEARCH_CONFIG
        ):
            query_type = "semantic"

        # Set filter
        search_filter = f"ApplicationID eq {application_id} and RunID eq {run_id}"
        filter = None
        userToken = None
        if AZURE_SEARCH_PERMITTED_GROUPS_COLUMN:
            userToken = request.headers.get("X-MS-TOKEN-AAD-ACCESS-TOKEN", "")
            logging.debug(f"USER TOKEN is {'present' if userToken else 'not present'}")
            if not userToken:
                raise Exception(
                    "Document-level access control is enabled, but user access token could not be fetched."
                )

            filter = generateFilterString(userToken)
            # filter = "ApplicationID eq 116 and RunID eq 113"
            # filter = "ApplicationID eq 108 and RunID eq 73"
            logging.debug(f"FILTER: {filter}")
            logging.debug(f"SEARCH FILTER: {search_filter}")

        # Set authentication
        authentication = {}
        if AZURE_SEARCH_KEY:
            authentication = {"type": "api_key", "api_key": AZURE_SEARCH_KEY}
        else:
            # If key is not provided, assume AOAI resource identity has been granted access to the search service
            authentication = {"type": "system_assigned_managed_identity"}

        data_source = {
            "type": "azure_search",
            "parameters": {
                "endpoint": f"https://{AZURE_SEARCH_SERVICE}.search.windows.net",
                "authentication": authentication,
                "index_name": AZURE_SEARCH_INDEX,
                "fields_mapping": {
                    "content_fields": (
                        parse_multi_columns(AZURE_SEARCH_CONTENT_COLUMNS)
                        if AZURE_SEARCH_CONTENT_COLUMNS
                        else []
                    ),
                    "title_field": (
                        AZURE_SEARCH_TITLE_COLUMN if AZURE_SEARCH_TITLE_COLUMN else None
                    ),
                    "url_field": (
                        AZURE_SEARCH_URL_COLUMN if AZURE_SEARCH_URL_COLUMN else None
                    ),
                    "filepath_field": (
                        AZURE_SEARCH_FILENAME_COLUMN
                        if AZURE_SEARCH_FILENAME_COLUMN
                        else None
                    ),
                    "vector_fields": (
                        parse_multi_columns(AZURE_SEARCH_VECTOR_COLUMNS)
                        if AZURE_SEARCH_VECTOR_COLUMNS
                        else []
                    ),
                },
                "in_scope": (
                    True if AZURE_SEARCH_ENABLE_IN_DOMAIN.lower() == "true" else False
                ),
                "top_n_documents": (
                    int(AZURE_SEARCH_TOP_K) if AZURE_SEARCH_TOP_K else int(SEARCH_TOP_K)
                ),
                "query_type": query_type,
                "semantic_configuration": (
                    AZURE_SEARCH_SEMANTIC_SEARCH_CONFIG
                    if AZURE_SEARCH_SEMANTIC_SEARCH_CONFIG
                    else ""
                ),
                "role_information": SYSTEM_MESSAGE, # AZURE_OPENAI_SYSTEM_MESSAGE,
                "filter": filter,
                "strictness": (
                    int(AZURE_SEARCH_STRICTNESS)
                    if AZURE_SEARCH_STRICTNESS
                    else int(SEARCH_STRICTNESS)
                ),
            },
        }

    else:
        raise Exception(
            f"DATASOURCE_TYPE is not configured or unknown: {DATASOURCE_TYPE}"
        )

    if "vector" in query_type.lower() and DATASOURCE_TYPE != "AzureMLIndex":
        embeddingDependency = {}
        if AZURE_OPENAI_EMBEDDING_NAME:
            embeddingDependency = {
                "type": "deployment_name",
                "deployment_name": AZURE_OPENAI_EMBEDDING_NAME,
            }
        elif AZURE_OPENAI_EMBEDDING_ENDPOINT and AZURE_OPENAI_EMBEDDING_KEY:
            embeddingDependency = {
                "type": "endpoint",
                "endpoint": AZURE_OPENAI_EMBEDDING_ENDPOINT,
                "authentication": {
                    "type": "api_key",
                    "key": AZURE_OPENAI_EMBEDDING_KEY,
                },
            }

        else:
            raise Exception(
                f"Vector query type ({query_type}) is selected for data source type {DATASOURCE_TYPE} but no embedding dependency is configured"
            )
        data_source["parameters"]["embedding_dependency"] = embeddingDependency

    return data_source


def prepare_model_args(request_body,application_id, run_id):
    request_messages = request_body.get("messages", [])
    messages = []
    if not SHOULD_USE_DATA:
        messages = [{"role": "system", "content": SYSTEM_MESSAGE}] # AZURE_OPENAI_SYSTEM_MESSAGE,

    for message in request_messages:
        if message:
            messages.append({"role": message["role"], "content": message["content"]})
    model_args = {
        "messages": messages,
        "temperature": float(AZURE_OPENAI_TEMPERATURE),
        "max_tokens": int(AZURE_OPENAI_MAX_TOKENS),
        "top_p": float(AZURE_OPENAI_TOP_P),
        "stop": (
            parse_multi_columns(AZURE_OPENAI_STOP_SEQUENCE)
            if AZURE_OPENAI_STOP_SEQUENCE
            else None
        ),
        "stream": SHOULD_STREAM,
        "model": AZURE_OPENAI_MODEL,
    }

    if SHOULD_USE_DATA:
        model_args["extra_body"] = {"data_sources": [get_configured_data_source(application_id, run_id)]}

    model_args_clean = copy.deepcopy(model_args)
    if model_args_clean.get("extra_body"):
        secret_params = [
            "key",
            "connection_string",
            "embedding_key",
            "encoded_api_key",
            "api_key",
        ]
        for secret_param in secret_params:
            if model_args_clean["extra_body"]["data_sources"][0]["parameters"].get(
                secret_param
            ):
                model_args_clean["extra_body"]["data_sources"][0]["parameters"][
                    secret_param
                ] = "*****"
        authentication = model_args_clean["extra_body"]["data_sources"][0][
            "parameters"
        ].get("authentication", {})
        for field in authentication:
            if field in secret_params:
                model_args_clean["extra_body"]["data_sources"][0]["parameters"][
                    "authentication"
                ][field] = "*****"
        embeddingDependency = model_args_clean["extra_body"]["data_sources"][0][
            "parameters"
        ].get("embedding_dependency", {})
        if "authentication" in embeddingDependency:
            for field in embeddingDependency["authentication"]:
                if field in secret_params:
                    model_args_clean["extra_body"]["data_sources"][0]["parameters"][
                        "embedding_dependency"
                    ]["authentication"][field] = "*****"

    logging.debug(f"REQUEST BODY: {json.dumps(model_args_clean, indent=4)}")

    return model_args


async def promptflow_request(request,application_id, run_id):
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {PROMPTFLOW_API_KEY}",
        }
        # Adding timeout for scenarios where response takes longer to come back
        logging.debug(f"Setting timeout to {PROMPTFLOW_RESPONSE_TIMEOUT}")
        async with httpx.AsyncClient(
            timeout=float(PROMPTFLOW_RESPONSE_TIMEOUT)
        ) as client:
            pf_formatted_obj = convert_to_pf_format(
                request, application_id, run_id,
                PROMPTFLOW_REQUEST_FIELD_NAME, 
                PROMPTFLOW_RESPONSE_FIELD_NAME
            )
            # NOTE: This only support question and chat_history parameters
            # If you need to add more parameters, you need to modify the request body
            response = await client.post(
                PROMPTFLOW_ENDPOINT,
                json={
                    f"{PROMPTFLOW_REQUEST_FIELD_NAME}": pf_formatted_obj[-1]["inputs"][
                        PROMPTFLOW_REQUEST_FIELD_NAME
                    ],
                    "chat_history": pf_formatted_obj[:-1],
                },
                headers=headers,
            )
        resp = response.json()
        resp["id"] = request["messages"][-1]["id"]
        return resp
    except Exception as e:
        logging.error(f"An error occurred while making promptflow_request: {e}")


async def send_chat_request(request, application_id, run_id):
    filtered_messages = []
    messages = request.get("messages", [])

    for message in messages:
        if message.get("role") != 'tool':
            filtered_messages.append(message)

    request['messages'] = filtered_messages
    model_args = prepare_model_args(request,application_id, run_id)

    try:
        azure_openai_client = init_openai_client()
        raw_response = await azure_openai_client.chat.completions.with_raw_response.create(**model_args)
        response = raw_response.parse()
        apim_request_id = raw_response.headers.get("apim-request-id")
    except Exception as e:
        logging.exception("Exception in send_chat_request")
        raise e
    return response, apim_request_id


async def complete_chat_request(request_body, application_id, run_id):
    if USE_PROMPTFLOW and PROMPTFLOW_ENDPOINT and PROMPTFLOW_API_KEY:
        response = await promptflow_request(request_body,application_id, run_id)
        history_metadata = request_body.get("history_metadata", {})
        return format_pf_non_streaming_response(
            response, history_metadata,
            PROMPTFLOW_RESPONSE_FIELD_NAME,
            PROMPTFLOW_CITATIONS_FIELD_NAME
        )
    else:
        response, apim_request_id = await send_chat_request(request_body,application_id, run_id)
        history_metadata = request_body.get("history_metadata", {})
        return format_non_streaming_response(
            response,
            history_metadata,
            apim_request_id
        )


async def stream_chat_request(request_body, application_id, run_id):
    response, apim_request_id = await send_chat_request(request_body, application_id, run_id)
    history_metadata = request_body.get("history_metadata", {})

    async def generate():
        async for completionChunk in response:
            yield format_stream_response(
                completionChunk,
                history_metadata,
                apim_request_id
            )

    return generate()


async def conversation_internal(request_body, application_id, run_id):
    try:
        if SHOULD_STREAM:
            result = await stream_chat_request(request_body, application_id, run_id)
            response = await make_response(format_as_ndjson(result))
            response.timeout = None
            response.mimetype = "application/json-lines"
            return response
        else:
            result = await complete_chat_request(request_body, application_id, run_id)
            return jsonify(result)

    except Exception as ex:
        logging.exception(ex)
        if hasattr(ex, "status_code"):
            return jsonify({"error": str(ex)}), ex.status_code
        else:
            return jsonify({"error": str(ex)}), 500


# # Conversation History API deleted ##


# @bp.route("/history/read", methods=["POST"])
# async def get_conversation():
#     authenticated_user = get_authenticated_user_details(request_headers=request.headers)
#     user_id = authenticated_user["user_principal_id"]

#     ## check request for conversation_id
#     request_json = await request.get_json()
#     conversation_id = request_json.get("conversation_id", None)

#     if not conversation_id:
#         return jsonify({"error": "conversation_id is required"}), 400

#     ## make sure cosmos is configured
#     cosmos_conversation_client = init_cosmosdb_client()
#     if not cosmos_conversation_client:
#         raise Exception("CosmosDB is not configured or not working")

#     ## get the conversation object and the related messages from cosmos
#     conversation = await cosmos_conversation_client.get_conversation(
#         user_id, conversation_id
#     )
#     ## return the conversation id and the messages in the bot frontend format
#     if not conversation:
#         return (
#             jsonify(
#                 {
#                     "error": f"Conversation {conversation_id} was not found. It either does not exist or the logged in user does not have access to it."
#                 }
#             ),
#             404,
#         )

#     # get the messages for the conversation from cosmos
#     conversation_messages = await cosmos_conversation_client.get_messages(
#         user_id, conversation_id
#     )

#     ## format the messages in the bot frontend format
#     messages = [
#         {
#             "id": msg["id"],
#             "role": msg["role"],
#             "content": msg["content"],
#             "createdAt": msg["createdAt"],
#             "feedback": msg.get("feedback"),
#         }
#         for msg in conversation_messages
#     ]

#     await cosmos_conversation_client.cosmosdb_client.close()
#     return jsonify({"conversation_id": conversation_id, "messages": messages}), 200


# @bp.route("/history/rename", methods=["POST"])
# async def rename_conversation():
#     authenticated_user = get_authenticated_user_details(request_headers=request.headers)
#     user_id = authenticated_user["user_principal_id"]

#     ## check request for conversation_id
#     request_json = await request.get_json()
#     conversation_id = request_json.get("conversation_id", None)

#     if not conversation_id:
#         return jsonify({"error": "conversation_id is required"}), 400

#     ## make sure cosmos is configured
#     cosmos_conversation_client = init_cosmosdb_client()
#     if not cosmos_conversation_client:
#         raise Exception("CosmosDB is not configured or not working")

#     ## get the conversation from cosmos
#     conversation = await cosmos_conversation_client.get_conversation(
#         user_id, conversation_id
#     )
#     if not conversation:
#         return (
#             jsonify(
#                 {
#                     "error": f"Conversation {conversation_id} was not found. It either does not exist or the logged in user does not have access to it."
#                 }
#             ),
#             404,
#         )

#     ## update the title
#     title = request_json.get("title", None)
#     if not title:
#         return jsonify({"error": "title is required"}), 400
#     conversation["title"] = title
#     updated_conversation = await cosmos_conversation_client.upsert_conversation(
#         conversation
#     )

#     await cosmos_conversation_client.cosmosdb_client.close()
#     return jsonify(updated_conversation), 200


# @bp.route("/history/delete_all", methods=["DELETE"])
# async def delete_all_conversations():
#     ## get the user id from the request headers
#     authenticated_user = get_authenticated_user_details(request_headers=request.headers)
#     user_id = authenticated_user["user_principal_id"]

#     # get conversations for user
#     try:
#         ## make sure cosmos is configured
#         cosmos_conversation_client = init_cosmosdb_client()
#         if not cosmos_conversation_client:
#             raise Exception("CosmosDB is not configured or not working")

#         conversations = await cosmos_conversation_client.get_conversations(
#             user_id, offset=0, limit=None
#         )
#         if not conversations:
#             return jsonify({"error": f"No conversations for {user_id} were found"}), 404

#         # delete each conversation
#         for conversation in conversations:
#             ## delete the conversation messages from cosmos first
#             deleted_messages = await cosmos_conversation_client.delete_messages(
#                 conversation["id"], user_id
#             )

#             ## Now delete the conversation
#             deleted_conversation = await cosmos_conversation_client.delete_conversation(
#                 user_id, conversation["id"]
#             )
#         await cosmos_conversation_client.cosmosdb_client.close()
#         return (
#             jsonify(
#                 {
#                     "message": f"Successfully deleted conversation and messages for user {user_id}"
#                 }
#             ),
#             200,
#         )

#     except Exception as e:
#         logging.exception("Exception in /history/delete_all")
#         return jsonify({"error": str(e)}), 500


# @bp.route("/history/clear", methods=["POST"])
# async def clear_messages():
#     ## get the user id from the request headers
#     authenticated_user = get_authenticated_user_details(request_headers=request.headers)
#     user_id = authenticated_user["user_principal_id"]

#     ## check request for conversation_id
#     request_json = await request.get_json()
#     conversation_id = request_json.get("conversation_id", None)

#     try:
#         if not conversation_id:
#             return jsonify({"error": "conversation_id is required"}), 400

#         ## make sure cosmos is configured
#         cosmos_conversation_client = init_cosmosdb_client()
#         if not cosmos_conversation_client:
#             raise Exception("CosmosDB is not configured or not working")

#         ## delete the conversation messages from cosmos
#         deleted_messages = await cosmos_conversation_client.delete_messages(
#             conversation_id, user_id
#         )

#         return (
#             jsonify(
#                 {
#                     "message": "Successfully deleted messages in conversation",
#                     "conversation_id": conversation_id,
#                 }
#             ),
#             200,
#         )
#     except Exception as e:
#         logging.exception("Exception in /history/clear_messages")
#         return jsonify({"error": str(e)}), 500


# @bp.route("/history/ensure", methods=["GET"])
# async def ensure_cosmos():
#     if not AZURE_COSMOSDB_ACCOUNT:
#         return jsonify({"error": "CosmosDB is not configured"}), 404

#     try:
#         cosmos_conversation_client = init_cosmosdb_client()
#         success, err = await cosmos_conversation_client.ensure()
#         if not cosmos_conversation_client or not success:
#             if err:
#                 return jsonify({"error": err}), 422
#             return jsonify({"error": "CosmosDB is not configured or not working"}), 500

#         await cosmos_conversation_client.cosmosdb_client.close()
#         return jsonify({"message": "CosmosDB is configured and working"}), 200
#     except Exception as e:
#         logging.exception("Exception in /history/ensure")
#         cosmos_exception = str(e)
#         if "Invalid credentials" in cosmos_exception:
#             return jsonify({"error": cosmos_exception}), 401
#         elif "Invalid CosmosDB database name" in cosmos_exception:
#             return (
#                 jsonify(
#                     {
#                         "error": f"{cosmos_exception} {AZURE_COSMOSDB_DATABASE} for account {AZURE_COSMOSDB_ACCOUNT}"
#                     }
#                 ),
#                 422,
#             )
#         elif "Invalid CosmosDB container name" in cosmos_exception:
#             return (
#                 jsonify(
#                     {
#                         "error": f"{cosmos_exception}: {AZURE_COSMOSDB_CONVERSATIONS_CONTAINER}"
#                     }
#                 ),
#                 422,
#             )
#         else:
#             return jsonify({"error": "CosmosDB is not working"}), 500


# async def generate_title(conversation_messages):
#     ## make sure the messages are sorted by _ts descending
#     title_prompt = 'Summarize the conversation so far into a 4-word or less title. Do not use any quotation marks or punctuation. Respond with a json object in the format {{"title": string}}. Do not include any other commentary or description.'

#     messages = [
#         {"role": msg["role"], "content": msg["content"]}
#         for msg in conversation_messages
#     ]
#     messages.append({"role": "user", "content": title_prompt})

#     try:
#         azure_openai_client = init_openai_client(use_data=False)
#         response = await azure_openai_client.chat.completions.create(
#             model=AZURE_OPENAI_MODEL, messages=messages, temperature=1, max_tokens=64
#         )

#         title = json.loads(response.choices[0].message.content)["title"]
#         return title
#     except Exception as e:
#         return messages[-2]["content"]
