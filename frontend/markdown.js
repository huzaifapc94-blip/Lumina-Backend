function escapeHtml(str) {
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

/**
 * Renders basic markdown including lists, headers, bold, inline code, and fenced code blocks.
 * @param {string} text 
 * @returns {string} Safe HTML string
 */
function renderMarkdown(text) {
    if (!text) return "";

    // Split text into code blocks and other text
    const parts = text.split(/(```[\s\S]*?```)/g);
    
    return parts.map(part => {
        if (part.startsWith('```')) {
            // Find the ending ticks and extract the language + content
            const match = part.match(/```(\w*)\n([\s\S]*?)```/);
            if (match) {
                const lang = match[1] || 'text';
                const code = match[2].trim();
                const escapedCode = escapeHtml(code);
                
                return `
                    <div class="code-block-container">
                        <div class="code-block-header">
                            <span class="code-block-lang">${lang.toLowerCase()}</span>
                            <button class="copy-code-btn" onclick="copyCode(this)">
                                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="copy-icon"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                                <span>Copy Code</span>
                            </button>
                        </div>
                        <pre><code class="language-${lang}">${escapedCode}</code></pre>
                    </div>
                `;
            }
            // Fallback for malformed block
            return `<pre><code>${escapeHtml(part)}</code></pre>`;
        } else {
            // Parse ordinary text
            let html = part;

            // Escape HTML entities to prevent XSS except for tags we inject
            html = escapeHtml(html);

            // Inline Code: `code`
            html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

            // Bold: **text**
            html = html.replace(/\*\*([\s\S]*?)\*\*/g, '<strong>$1</strong>');

            // Italic: *text* or _text_
            html = html.replace(/\*([\s\S]*?)\*/g, '<em>$1</em>');
            html = html.replace(/_([\s\S]*?)_/g, '<em>$1</em>');

            // Split into lines to parse Block elements
            const lines = html.split('\n');
            let resultLines = [];
            let inList = false;

            for (let i = 0; i < lines.length; i++) {
                let line = lines[i];

                // Headers
                if (line.startsWith('#### ')) {
                    line = `<h4>${line.slice(5)}</h4>`;
                } else if (line.startsWith('### ')) {
                    line = `<h3>${line.slice(4)}</h3>`;
                } else if (line.startsWith('## ')) {
                    line = `<h2>${line.slice(3)}</h2>`;
                } else if (line.startsWith('# ')) {
                    line = `<h1>${line.slice(2)}</h1>`;
                }
                // Bullet List: matches "- item" or "* item"
                else if (line.startsWith('- ') || line.startsWith('* ')) {
                    if (!inList) {
                        resultLines.push('<ul>');
                        inList = true;
                    }
                    line = `<li>${line.slice(2)}</li>`;
                } 
                // Number List: matches "1. item", "2. item", etc.
                else if (/^\d+\.\s/.test(line)) {
                    if (!inList) {
                        resultLines.push('<ol>');
                        inList = true;
                    }
                    const dotIndex = line.indexOf('.');
                    line = `<li>${line.slice(dotIndex + 2)}</li>`;
                }
                else {
                    if (inList) {
                        // Close active list block
                        const lastListTag = resultLines[resultLines.length - 1];
                        if (resultLines.join('').includes('<ul>') && !resultLines.join('').includes('</ul>')) {
                            resultLines.push('</ul>');
                        } else if (resultLines.join('').includes('<ol>') && !resultLines.join('').includes('</ol>')) {
                            resultLines.push('</ol>');
                        }
                        inList = false;
                    }
                }

                resultLines.push(line);
            }

            if (inList) {
                // If list goes until the end of this block
                resultLines.push('</ul>');
            }

            // Join and wrap paragraph blocks
            let finalHtml = resultLines.join('\n');
            
            // Clean up empty lines or format double line breaks as paragraphs
            const paragraphs = finalHtml.split(/\n{2,}/g);
            return paragraphs.map(p => {
                const trimmed = p.trim();
                if (!trimmed) return '';
                // If it already starts with block elements, don't wrap in <p>
                if (/^(<h|<ul|<ol|<div|<pre|<li)/i.test(trimmed)) {
                    return trimmed;
                }
                return `<p>${trimmed.replace(/\n/g, '<br>')}</p>`;
            }).join('');
        }
    }).join('');
}

/**
 * Copy code contents to clipboard
 * @param {HTMLButtonElement} button 
 */
function copyCode(button) {
    const container = button.closest('.code-block-container');
    const codeEl = container.querySelector('code');
    if (!codeEl) return;

    // Use textContent to get clean original code text without HTML markup
    const codeText = codeEl.textContent;

    navigator.clipboard.writeText(codeText).then(() => {
        const textSpan = button.querySelector('span');
        const originalText = textSpan.textContent;
        textSpan.textContent = 'Copied!';
        button.classList.add('copied');
        
        setTimeout(() => {
            textSpan.textContent = originalText;
            button.classList.remove('copied');
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy code: ', err);
    });
}
