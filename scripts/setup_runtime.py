"""First-use checks without inline shell/Python quoting dependencies."""
import argparse
from pathlib import Path
import sys

REQUIRED_EMBEDDING=('model.safetensors','config.json','tokenizer.json','modules.json','1_Pooling/config.json')


def embedding_complete(folder):
    folder=Path(folder)
    return all((folder/name).is_file() and (folder/name).stat().st_size>0 for name in REQUIRED_EMBEDDING)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['check-python','probe-backend','download-embedding'])
    parser.add_argument('--target',type=Path)
    args=parser.parse_args()
    if sys.version_info[:2]!=(3,11):raise RuntimeError('Python 3.11 is required')
    if args.action=='probe-backend':
        import fastapi,uvicorn,yaml,httpx,langgraph,faiss,sentence_transformers,markdown_it
        print('Bundled backend ready')
    elif args.action=='download-embedding':
        if not args.target:parser.error('--target is required')
        if not embedding_complete(args.target):
            import os
            os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id='Qwen/Qwen3-Embedding-0.6B',local_dir=str(args.target),allow_patterns=['*.json','model.safetensors','*.txt','LICENSE','README.md'])
        if not embedding_complete(args.target):raise RuntimeError('Embedding files are incomplete')
        print('Embedding files ready')


if __name__=='__main__':main()
