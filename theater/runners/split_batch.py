import json
import os

with open('batches/batch_002/batch.json', 'r', encoding='utf-8') as f:
    tasks = json.load(f)

for i, task in enumerate(tasks):
    task_id = f"{i:03d}"
    json_path = f"batches/batch_002/tasks/task-{task_id}.json"
    prompt_path = f"batches/batch_002/tasks/task-{task_id}.prompt.txt"
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(task, f, ensure_ascii=False, indent=2)
        
    with open(prompt_path, 'w', encoding='utf-8') as f:
        f.write(task['prompt'])
