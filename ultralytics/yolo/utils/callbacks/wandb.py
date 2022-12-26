from ultralytics.yolo.utils.torch_utils import get_flops, get_num_params

try:
    import wandb

    assert hasattr(wandb, '__version__')
except (ImportError, AssertionError):
    wandb = None


def on_train_start(trainer):
    wandb.init(project=trainer.args.project if trainer.args.project != 'runs/train' else 'YOLOv8',
               name=trainer.args.name,
               config=dict(trainer.args)) if not wandb.run else wandb.run


def on_batch_end(trainer):
    wandb.run.log(trainer.label_loss_items(trainer.tloss, prefix="train"), step=trainer.epoch + 1)


def on_val_end(trainer):
    wandb.run.log(trainer.metrics, step=trainer.epoch + 1)
    if trainer.epoch == 0:
        model_info = {
            "Model/Parameters": get_num_params(trainer.model),
            "Model/GFLOPs": round(get_flops(trainer.model), 1),
            "Model/Inference speed (ms/img)": round(trainer.validator.speed[1], 1)}
        wandb.run.log(model_info, step=trainer.epoch + 1)


def on_train_epoch_end(trainer):
    if trainer.epoch == 1:
        wandb.run.log({f.stem: wandb.Image(str(f))
                       for f in trainer.save_dir.glob('train_batch*.jpg')},
                      step=trainer.epoch + 1)


def on_train_end(trainer):
    art = wandb.Artifact(type="model", name=f"run_{wandb.run.id}_model")
    if trainer.best.exists():
        art.add_file(trainer.best)
        wandb.run.log_artifact(art)


callbacks = {
    "on_train_start": on_train_start,
    "on_batch_end": on_batch_end,
    "on_train_epoch_end": on_train_epoch_end,
    "on_val_end": on_val_end,
    "on_train_end": on_train_end} if wandb else {}
