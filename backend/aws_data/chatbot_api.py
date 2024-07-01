import asyncio
from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage
import logging
import os
from dotenv import load_dotenv
import boto3
from langchain_community.retrievers import AmazonKendraRetriever
from backend.aws_data.constants import (
    INDEX_ID,
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    REGION_NAME,
    AI_TEMPERATURE,
    MODEL_ID,
    SYSTEM_MESSAGE,
    AI_TOP_P,
    AI_MAX_TOKENS,
    AI_SEARCH_TOP_K
)

load_dotenv()
logging.getLogger('botocore').setLevel(logging.WARNING)

class KendraSearcher:
    def __init__(self, index_id, aws_access_key_id, aws_secret_access_key):
        self.kendra_client = boto3.client('kendra', REGION_NAME,
                                          aws_access_key_id=aws_access_key_id,
                                          aws_secret_access_key=aws_secret_access_key)
        self.retriever = AmazonKendraRetriever(index_id=index_id, client=self.kendra_client,top_k=5)
        logging.info(f"KendraSearcher initialized")

    async def generate_response(self, context_string, query, no_results=False):
        if no_results:
            system_message_with_context = f"Question: {query}\nIt seems like your query does not relate to the specific application context. Please provide a query related to the application."
        else:
            if context_string:
                system_message_with_context = f"Context: {context_string}\nQuestion: {query}"
            else:
                system_message_with_context = f"Question: {query}"

        messages = [HumanMessage(content=system_message_with_context)]

        async def async_stream(sync_gen):
            for item in sync_gen:
                await asyncio.sleep(0)  
                yield item

        response_gen = async_stream(chat.stream(messages))  

        output = []

        citation_content = context_string if context_string else "No relevant citations found."
        citation_content += f"\nUser Query: {query}"
        output.append({
            "role": "tool",
            "content": "{\"citations\": [" + citation_content + "]}"
        })

        async for response in response_gen:
            print(response.content, end="")
            output.append({
                "choices": [
                    {
                        "messages": [
                            {
                                "role": "assistant",
                                "content": response.content  
                            }
                        ]
                    }
                ]
            })
        return output

    async def search(self, request_messages, application_id, run_id):
        query_text = request_messages['messages'][0]['content']
        
        response = self.kendra_client.retrieve(
            QueryText=query_text,
            IndexId=self.retriever.index_id,
            # PageSize=self.retriever.top_k,
            AttributeFilter={
                'AndAllFilters': [
                    {
                        'EqualsTo': {
                            'Key': 'ApplicationID',
                            'Value': {'LongValue': int(application_id)}
                        }
                    },
                    {
                        'EqualsTo': {
                            'Key': 'RunID',
                            'Value': {'LongValue': int(run_id)}
                        }
                    }
                ]
            },
            RequestedDocumentAttributes=['ScoreAttributes']
        )
        data = self.extract_relevant_results(response)
        
        if not data:
            return await self.generate_response("", query_text, no_results=True)
        
        context_string = self.format_context(data)
        return await self.generate_response(context_string, query_text)

    def extract_relevant_results(self, response):
        data = []
        if 'ResultItems' in response:
            data.extend([result for result in response['ResultItems'] if 'ScoreAttributes' in result and result['ScoreAttributes']['ScoreConfidence'] in ('VERY_HIGH', 'HIGH', 'MEDIUM')])
        return data

    def format_context(self, data):
        context_data = []
        for result in data:
            context_data.append({
                "content": result.get('Content', ''),
                "title": result.get('DocumentTitle', None),  
                "url": result.get('DocumentURI', 'null' if not result.get('DocumentURI') else result['DocumentURI']),
                "filepath": result.get('DocumentId', None),
                "chunk_id": result.get('Id', None)
            })
        return ', '.join([str(item) for item in context_data])

chat = ChatBedrock(
    model_id=MODEL_ID,
    system_prompt_with_tools=SYSTEM_MESSAGE,
    model_kwargs={"temperature": float(AI_TEMPERATURE),
                  "top_p":float(AI_TOP_P),
                  "max_tokens":int(AI_MAX_TOKENS),
                  "top_k": int(AI_SEARCH_TOP_K)},
    region_name=REGION_NAME
)

kendra_searcher = KendraSearcher(INDEX_ID,
                                 AWS_ACCESS_KEY_ID,
                                 AWS_SECRET_ACCESS_KEY)