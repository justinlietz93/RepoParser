import streamlit as st
import json
from pathlib import Path
import logging
from backend.core.crawler import RepositoryCrawler
from backend.core.tokenizer import TokenAnalyzer

logger = logging.getLogger(__name__)

def calculate_costs(total_tokens, model="gpt-3.5-turbo"):
    """Calculate estimated costs based on token count and model."""
    rates = {
        "gpt-3.5-turbo": {"input": 0.0015, "output": 0.002},
        "gpt-4": {"input": 0.03, "output": 0.06},
        "gpt-4-32k": {"input": 0.06, "output": 0.12}
    }
    
    rate = rates.get(model, rates["gpt-3.5-turbo"])
    input_cost = (total_tokens / 1000) * rate["input"]
    output_cost = (total_tokens / 1000) * rate["output"]
    
    return input_cost, output_cost

def get_file_contents(file_path):
    """Read and return file contents safely."""
    try:
        if not Path(file_path).is_file():
            logger.warning(f"Not a file or not accessible: {file_path}")
            return None
            
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                logger.warning(f"Empty file: {file_path}")
                return None
            return content
    except UnicodeDecodeError:
        logger.warning(f"Binary or non-UTF-8 file skipped: {file_path}")
        return None
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {str(e)}")
        return None

def build_codebase_json(repo_path, config):
    """Build JSON representation of the codebase."""
    try:
        crawler = RepositoryCrawler(repo_path, config)
        analyzer = TokenAnalyzer()
        codebase_dict = {}
        total_tokens = 0
        
        # Get list of files from crawler
        try:
            files_info = crawler.walk()
            if not files_info:
                logger.warning("No files found in repository")
                return {}, 0
                
            for file_path, size in files_info:
                try:
                    full_path = Path(repo_path) / file_path
                    if not full_path.is_file():
                        logger.warning(f"File not found or not accessible: {full_path}")
                        continue
                        
                    content = get_file_contents(full_path)
                    if content is None:
                        continue
                        
                    codebase_dict[str(file_path)] = {
                        'content': content,
                        'size': size,
                    }
                    total_tokens += analyzer.count_tokens(content)
                except Exception as e:
                    logger.error(f"Error processing {file_path}: {str(e)}")
                    continue
            
            if not codebase_dict:
                logger.warning("No valid files processed")
                return {}, 0
                
            return codebase_dict, total_tokens
            
        except Exception as e:
            logger.error(f"Error walking repository: {str(e)}")
            raise Exception(f"Error scanning repository: {str(e)}")
            
    except Exception as e:
        logger.error(f"Error initializing repository crawler: {str(e)}")
        raise Exception(f"Error accessing repository: {str(e)}")

def build_directory_tree(codebase_dict):
    """Build a formatted directory tree structure."""
    # Convert flat paths to tree structure
    tree = {}
    for path in codebase_dict.keys():
        parts = Path(path).parts
        current = tree
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = None
    
    # Format tree as string
    lines = []
    
    # Add parent directory name
    parent_dir = Path(st.session_state.config.get('local_root', '')).name
    lines.append(f"{parent_dir}/")
    
    def _format_tree(node, prefix="", is_last=True, indent=""):
        entries = sorted(node.items())
        for i, (name, subtree) in enumerate(entries):
            is_last_item = i == len(entries) - 1
            connector = "└── " if is_last_item else "├── "
            
            # Add the current item
            if subtree is None:  # File
                lines.append(f"{indent}{connector}{name}")
            else:  # Directory
                lines.append(f"{indent}{connector}{name}/")
                
                # Calculate new indent for children
                new_indent = indent + ("    " if is_last_item else "│   ")
                _format_tree(subtree, prefix + "  ", is_last_item, new_indent)
    
    # Start tree with initial indent
    _format_tree(tree, indent="    ")
    
    return lines

def build_prompt(codebase_dict):
    """Build a formatted prompt from the codebase contents."""
    # Start with repository structure
    prompt_parts = ["# Repository Structure\n"]
    
    # Add directory tree
    prompt_parts.append("```")
    prompt_parts.extend(build_directory_tree(codebase_dict))
    prompt_parts.append("```\n")
    
    # Add file contents in XML format
    prompt_parts.append("# Codebase XML\n")
    prompt_parts.append("```xml")
    prompt_parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    
    # Get parent directory name
    parent_dir = Path(st.session_state.config.get('local_root', '')).name
    prompt_parts.append(f'<{parent_dir}>')
    
    # Build a proper directory tree structure
    dir_tree = {}
    for path, info in sorted(codebase_dict.items()):
        parts = Path(path).parts
        current = dir_tree
        for part in parts[:-1]:  # Process directories
            if part not in current:
                current[part] = {}
            current = current[part]
        # Add file to its directory
        current[parts[-1]] = info
    
    def write_directory(tree, current_path="", indent_level=1):
        """Recursively write directory structure in XML format."""
        lines = []
        indent = "  " * indent_level
        
        # Process all entries
        for name, content in sorted(tree.items()):
            full_path = f"{parent_dir}/{current_path}/{name}".replace("//", "/")
            
            # If content is a dict without 'content' key, it's a directory
            if isinstance(content, dict) and 'content' not in content:
                lines.append(f'{indent}<{full_path}>')
                lines.extend(write_directory(content, f"{current_path}/{name}".lstrip("/"), indent_level + 1))
                lines.append(f'{indent}</{full_path}>')
            else:  # It's a file
                lines.append(f'{indent}<{full_path}>')
                lines.append(f'{indent}  <![CDATA[')
                # Indent the content
                content_lines = content["content"].splitlines()
                if content_lines:
                    lines.extend(f'{indent}    {line}' for line in content_lines)
                else:
                    lines.append(f'{indent}    {content["content"]}')
                lines.append(f'{indent}  ]]>')
                lines.append(f'{indent}</{full_path}>')
        
        return lines
    
    # Generate XML content
    prompt_parts.extend(write_directory(dir_tree))
    prompt_parts.append(f'</{parent_dir}>')
    prompt_parts.append("```\n")
    
    return "\n".join(prompt_parts)

def render_codebase_view():
    """Render the codebase view page."""
    st.title("Codebase Overview 📚")
    
    # Get repository path from session state
    repo_path = st.session_state.config.get('local_root', '')
    if not repo_path:
        st.warning("Please set a repository path in the sidebar first.")
        return
    
    # Model selection
    model = st.selectbox(
        "Select Token Analysis Model",
        options=["gpt-3.5-turbo", "gpt-4", "gpt-4-32k"],
        index=0
    )
    
    if st.button("Generate Codebase Overview"):
        with st.spinner("Analyzing codebase..."):
            try:
                # Build codebase JSON
                codebase_dict, total_tokens = build_codebase_json(
                    repo_path,
                    st.session_state.config
                )
                
                # Calculate costs
                input_cost, output_cost = calculate_costs(total_tokens, model)
                
                # Display token analysis
                st.subheader("Token Analysis")
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Total Tokens", f"{total_tokens:,}")
                with col2:
                    st.metric("Input Cost", f"${input_cost:.2f}")
                with col3:
                    st.metric("Output Cost", f"${output_cost:.2f}")
                with col4:
                    total_size = sum(file_info['size'] for file_info in codebase_dict.values())
                    st.metric("Total Size", f"{total_size / 1024:.1f} KB")
                
                # Build and display the prompt
                st.subheader("Codebase Prompt")
                st.text("Use this prompt to provide codebase context to AI")
                
                # Create and display the formatted prompt
                prompt = build_prompt(codebase_dict)
                st.code(prompt, language="markdown")
                
            except Exception as e:
                logger.error(f"Error generating codebase overview: {str(e)}")
                st.error(f"Error generating codebase overview: {str(e)}")

if __name__ == "__main__":
    render_codebase_view() 