"""Model-only view of a tool result; retain original data for journals and UI."""
import json


def model_result(result):
    """Drop only text blocks that exactly repeat structuredContent as JSON.

    Preserve independent text, non-text blocks, errors and metadata. No mutation
    of the original response: tool_result.result remains the complete artifact.
    """
    if not isinstance(result,dict) or 'structuredContent' not in result or not isinstance(result.get('content'),list):
        return result
    def duplicate(block):
        if not isinstance(block,dict) or set(block)!={'type','text'} or block['type']!='text' or not isinstance(block['text'],str):
            return False
        try:return json.loads(block['text'])==result['structuredContent']
        except (ValueError,TypeError):return False
    content=[block for block in result['content'] if not duplicate(block)]
    if len(content)==len(result['content']):return result
    view={key:value for key,value in result.items() if key!='content'}
    if content:view['content']=content
    return view
