python -m sgd.batch_run new --log_file logs/sgd.jsonl --score_table_file logs/sgd.md --max_dialog 100
python -m sgd.batch_run recover --log_file logs/sgd.jsonl --score_table_file logs/sgd.md

python -m sgd.metric --log_file logs/sgd.jsonl --score_table_file logs/tmp.md
