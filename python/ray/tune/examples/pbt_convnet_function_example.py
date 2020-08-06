#!/usr/bin/env python

# __tutorial_imports_begin__
import argparse
import os
import numpy as np
import torch
import torch.optim as optim
from torchvision import datasets
from ray.tune.examples.mnist_pytorch import train, test, ConvNet,\
    get_data_loaders

import ray
from ray import tune
from ray.tune.schedulers import PopulationBasedTraining
from ray.tune.trial import ExportFormat

# __tutorial_imports_end__

def train_convnet(config, checkpoint_dir=None):
    step = 0
    train_loader, test_loader = get_data_loaders()
    model = ConvNet()
    optimizer = optim.SGD(
        model.parameters(),
        lr=config.get("lr", 0.01),
        momentum=config.get("momentum", 0.9)
    )

    if checkpoint_dir:
        path = os.path.join(checkpoint_dir, "checkpoint")
        checkpoint = torch.load(path)
        model.load_state_dict(checkpoint["model_state_dict"])
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        step = checkpoint["step"]

    for param_group in optimizer.param_groups:
        if "lr" in config:
            param_group["lr"] = config["lr"]
        if "momentum" in config:
            param_group["momentum"] = config["momentum"]

    while True:
        train(model, optimizer, train_loader)
        acc = test(model, test_loader)
        if step % 5 == 0:
            with tune.checkpoint_dir(step=step) as checkpoint_dir:
                path = os.path.join(checkpoint_dir, "checkpoint")
                torch.save({
                    "step": step,
                    "model_state_dict": model.state_dict(),
                    "optimizer_state_dict": optimizer.state_dict(),
                    "mean_accuracy": acc
                }, path)
        step += 1
        tune.report(
            mean_accuracy=acc,
        )

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--smoke-test", action="store_true", help="Finish quickly for testing")
    args, _ = parser.parse_known_args()

    ray.init(local_mode=True)
    datasets.MNIST("~/data", train=True, download=True)

    # __pbt_begin__
    scheduler = PopulationBasedTraining(
        time_attr="training_iteration",
        metric="mean_accuracy",
        mode="max",
        perturbation_interval=5,
        hyperparam_mutations={
            # distribution for resampling
            "lr": lambda: np.random.uniform(0.0001, 1),
            # allow perturbations within this set of categorical values
            "momentum": [0.8, 0.9, 0.99],
        })

    # __pbt_end__

    # __tune_begin__
    class CustomStopper(tune.Stopper):
        def __init__(self):
            self.should_stop = False

        def __call__(self, trial_id, result):
            max_iter = 5 if args.smoke_test else 100
            if not self.should_stop and result["mean_accuracy"] > 0.96:
                self.should_stop = True
            return self.should_stop or result["training_iteration"] >= max_iter

        def stop_all(self):
            return self.should_stop


    stopper = CustomStopper()

    analysis = tune.run(
        train_convnet,
        name="pbt_test",
        scheduler=scheduler,
        verbose=1,
        stop=stopper,
        export_formats=[ExportFormat.MODEL],
        checkpoint_score_attr="mean_accuracy",
        checkpoint_freq=5,
        keep_checkpoints_num=4,
        num_samples=4,
        config={
            "lr": tune.uniform(0.001, 1),
            "momentum": tune.uniform(0.001, 1),
        })
    # __tune_end__

    best_trial = analysis.get_best_trial("mean_accuracy")
    all_checkpoints = analysis.get_trial_checkpoints_paths(best_trial, "mean_accuracy")
    print(all_checkpoints)
    best_checkpoint_path = max(all_checkpoints, key=lambda x: x[1])
    best_model = ConvNet()
    best_checkpoint = torch.load(best_checkpoint_path[0])
    best_model.load_state_dict(best_checkpoint["model_state_dict"])
    # Note that test only runs on a small random set of the test data, thus the
    # accuracy may be different from metrics shown in tuning process.
    test_acc = test(best_model, get_data_loaders()[1])
    print("best model accuracy: ", test_acc)
