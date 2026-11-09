const promptArea=document.querySelector('[data-training-prompt]');
const status=document.querySelector('[data-copy-status]');
if(promptArea){fetch('training-prompt.txt').then(r=>{if(!r.ok)throw new Error('download');return r.text();}).then(text=>{promptArea.value=text;}).catch(()=>{status.textContent='Откройте текст промпта по ссылке ниже.';});}
document.querySelector('[data-copy-prompt]')?.addEventListener('click',async()=>{if(!promptArea?.value)return;try{await navigator.clipboard.writeText(promptArea.value);status.textContent='Промпт скопирован. Передайте его своему ИИ-агенту.';}catch{promptArea.focus();promptArea.select();status.textContent='Текст выделен. Скопируйте его обычным способом.';}});
