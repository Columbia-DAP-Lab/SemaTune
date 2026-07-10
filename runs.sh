sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_tpcc_dual_loop_example.json' --stream-output --cleanup-before-start
sudo mv results/ final_results/
sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_tpcc_dual_loop_example.json' --stream-output --cleanup-before-start --single-llm --replay final_results/dual_loop_history_tpcc_*
sudo mv results/ final_results-single-llm/
sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_tpcc_dual_loop_example.json' --stream-output --cleanup-before-start --fixed
sudo mv results/ final_results-fixed/

sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_tpcc_dual_loop_every5.json' --stream-output --cleanup-before-start
sudo mv results/ final_results_every5/
sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_tpcc_dual_loop_every5.json' --stream-output --cleanup-before-start --single-llm --replay final_results_every5/dual_loop_history_tpcc_*
sudo mv results/ final_results-single-llm-every5/
sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_tpcc_dual_loop_every5.json' --stream-output --cleanup-before-start --fixed
sudo mv results/ final_results-fixed-every5/

sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_tpcc_dual_loop_every1.json' --stream-output --cleanup-before-start
sudo mv results/ final_results_every1/
sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_tpcc_dual_loop_every1.json' --stream-output --cleanup-before-start --single-llm --replay final_results_every1/dual_loop_history_tpcc_*
sudo mv results/ final_results-single-llm-every1/
sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_tpcc_dual_loop_every1.json' --stream-output --cleanup-before-start --fixed
sudo mv results/ final_results-fixed-every1/

sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_tpcc_dual_loop_mixed_loads.json' --stream-output --cleanup-before-start
sudo mv results/ final_results_mixed/
sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_tpcc_dual_loop_mixed_loads.json' --stream-output --cleanup-before-start --single-llm --replay final_results_mixed/dual_loop_history_tpcc_*
sudo mv results/ final_results-single-llm-mixed/
sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_tpcc_dual_loop_mixed_loads.json' --stream-output --cleanup-before-start --fixed
sudo mv results/ final_results-fixed-mixed/

sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_ycsb_dual_loop_every5.json' --stream-output --cleanup-before-start
sudo mv results/ final_results_ycsb_every5/
sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_ycsb_dual_loop_every5.json' --stream-output --cleanup-before-start --single-llm --replay final_results_ycsb_every5/dual_loop_history_ycsb_*
sudo mv results/ final_results-ycsb-single-llm-every5/
sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_ycsb_dual_loop_every5.json' --stream-output --cleanup-before-start --fixed
sudo mv results/ final_results-ycsb-fixed-every5/

sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_ycsb_dual_loop_every1.json' --stream-output --cleanup-before-start
sudo mv results/ final_results_ycsb_every1/
sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_ycsb_dual_loop_every1.json' --stream-output --cleanup-before-start --single-llm --replay final_results_ycsb_every1/dual_loop_history_ycsb_*
sudo mv results/ final_results-ycsb-single-llm-every1/
sudo python src/optimizer/dual_loop_main.py -c '/users/gliargko/os-param-tuning/config/optimizer/iclr/optimizer_ycsb_dual_loop_every1.json' --stream-output --cleanup-before-start --fixed
sudo mv results/ final_results-ycsb-fixed-every1/