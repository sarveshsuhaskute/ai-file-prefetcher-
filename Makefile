.PHONY: test sim
test:
	python3 -m unittest discover -s tests
sim:
	python3 simulate.py
