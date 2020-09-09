from aws_cdk import (
    aws_ec2 as ec2,
    aws_s3 as s3,
    aws_rds as rds,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_elasticloadbalancingv2 as elb,
    core
)


class AirflowStack(core.Stack):
    """Contains all services if using a single stack."""


class AirflowFargate(core.Construct):
    def __init__(
            self,
            scope: core.Construct,
            id: str,
            cloudmap_namespace="airflow.com",
            log_prefix="airflow",
            vpc=None,
            bucket=None,
            log_driver=None,
            base_image=None,
            rds_instance=None,
            message_broker_service=None,
            message_broker_service_name="rabbitmq",
            max_worker_count=16,
            worker_target_memory_utilization=80,
            worker_target_cpu_utilization=80,
            worker_memory_scale_in_cooldown=10,
            worker_memory_scale_out_cooldown=10,
            worker_cpu_scale_in_cooldown=10,
            worker_cpu_scale_out_cooldown=10,
            **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        """
        Currently the code only supports single stack plan is to modularize it even further by
        allowing individual stack to be deployed.
        """
        airflow_stack = AirflowStack(self, "airflow-stack",
                                     env=core.Environment(account="973069700476", region="us-west-2"))

        vpc = vpc or ec2.Vpc.from_lookup(airflow_stack, "VPC", vpc_name="vpc-devprivcdp-dev-private")

        # Create a namespace in ECS with the above VPC and namespace.
        cloudmap_namespace_options = ecs.CloudMapNamespaceOptions(
            name=cloudmap_namespace, vpc=vpc
        )

        # Configure an S3 bucket with associated policy objects.
        bucket = bucket or s3.Bucket(
            airflow_stack,
            "airflow-bucket",
            bucket_name="airflow-logs",
            removal_policy=core.RemovalPolicy.DESTROY,
        )

        # Creates an CloudFormation Output value for this stack.
        core.CfnOutput(
            airflow_stack,
            "s3-log-bucket",
            value=f"https://s3.console.aws.amazon.com/s3/buckets/{bucket.bucket_name}",
            description="where worker logs are written to",
        )

        cluster = ecs.Cluster(
            airflow_stack,
            "cluster",
            vpc=vpc,
            default_cloud_map_namespace=cloudmap_namespace_options,
        )

        # This is pulling from docker hub directly
        base_image = base_image or ecs.ContainerImage.from_registry(
            "knowsuchagency/airflow-cdk"
        )
        # rabbit mq image

        '''
        Setup Postgres
        '''
        rds_instance = rds_instance or rds.DatabaseInstance(
            airflow_stack,
            "airflow-rds-instance",
            master_username=postgres_user,
            engine=rds.DatabaseInstanceEngine.POSTGRES,
            allocated_storage=10,
            database_name=postgres_db,
            master_user_password=core.SecretValue.plain_text(
                postgres_password
            ),
            vpc=vpc,
            instance_type=ec2.InstanceType("t3.micro"),
            deletion_protection=False, # Required in Prod.
            delete_automated_backups=True,
            removal_policy=core.RemovalPolicy.DESTROY,
        )

        '''
        Configure rabbit-mq alb
        '''
        rabbitmq_alb = elb.ApplicationLoadBalancer(airflow_stack,
                                                   "rabbitmq-alb",
                                                   vpc=vpc,
                                                   internet_facing=True)
        # cloud formation templates.
        core.CfnOutput(
            airflow_stack,
            id="rabbitmqManagement",
            value=f"http://{rabbitmq_alb.load_balancer_dns_name}",
        )
        # add rabbit mq listener
        rabbitmq_listener = rabbitmq_alb.add_listener(
            "rabbitmq-listener", port=80
        )

        '''
        Configure all the environment variables needed by airflow.
        '''

        # Creates a log driver configuration that sends log information to CloudWatch Logs.
        log_driver = log_driver or ecs.LogDriver.aws_logs(
            stream_prefix=log_prefix
        )

        env = {}
        postgres_hostname = rds_instance.db_instance_endpoint_address
        postgres_connection_suffix = f"://{postgres_user}:{postgres_password}@{postgres_hostname}:5432/{postgres_db}"
        connection_url = f"postgresql+psycopg2://{postgres_connection_suffix}"
        backend = f"db+postgresql://{postgres_connection_suffix}"
        message_broker_hostname = f"{message_broker_service_name}.{cloudmap_namespace}"
        broker_url = f"amqp://{message_broker_hostname}"

        env["AIRFLOW__CORE__SQL_ALCHEMY_CONN"] = connection_url
        env["AIRFLOW__CELERY__RESULT_BACKEND"] = backend
        env["AIRFLOW__CELERY__BROKER_URL"] = broker_url
        env = {k: str(v) for k, v in env.items()}

        '''
        Create a web service for airflow.
        '''
        web_container_service = ContainerService(airflow_stack)
        # create a task definition for the web service.
        web_container_service.add_task_definition("web-task",
                                                  cpu=1024,
                                                  memory_limit_mib=2048)
        # Grant permission to S3 bucket.
        bucket.grant_read_write(web_container_service.task_definition.task_role.grant_principal)
        # add container with appropriate task-definition for this task.
        web_container_service.add_container("web-container",
                                            base_image,
                                            env,
                                            log_driver)
        # create the fargate service
        web_container_service.fargate_service("web-service",
                                              cluster,
                                              is_alb=True)
        # configure the web-service
        web_container_service.service.target_group.configure_health_check(healthy_http_codes="200-399")
        # Open ports for the service

        '''
        Create worker service for airflow
        '''
        worker_container_service = ContainerService(airflow_stack)
        # create a task definition for worker service.
        worker_container_service.add_task_definition("worker-task", 1024, 2048)
        # Grant permission create / delete to S3 bucket.
        bucket.grant_read_write(worker_container_service.task_definition.task_role.grant_principal)
        bucket.grant_delete(worker_container_service.task_definition.task_role.grant_principal)
        # add container with appropriate task-definition for this task.
        worker_container_service.add_container("worker-container",
                                               base_image,
                                               environment=env,
                                               logging=log_driver,
                                               command=["worker"])
        # configure worker node as a service.
        worker_container_service.fargate_service("worker-service", cluster, desired_count=max_worker_count)
        # Add memory utilization scaler.
        worker_container_service.set_memory_utilization(max_worker_count,
                                                        "auto-scale-worker-memory",
                                                        policy_name="auto-scale-worker-memory",
                                                        target_utilization_percent=worker_target_memory_utilization,
                                                        scale_in_cooldown=worker_memory_scale_in_cooldown,
                                                        scale_out_cooldown=worker_memory_scale_out_cooldown)

        # Add cpu target utilization scaler.
        worker_container_service.set_cpu_utilization(max_worker_count, "auto-scale-worker-cpu",
                                                     policy_name="auto-scale-worker-cpu",
                                                     target_utilization_percent=worker_target_cpu_utilization,
                                                     scale_in_cooldown=worker_cpu_scale_in_cooldown,
                                                     scale_out_cooldown=worker_cpu_scale_out_cooldown)

        '''
        Create scheduler service for airflow
        '''
        scheduler_container_service = ContainerService(airflow_stack)
        # create a task definition for scheduler service.
        scheduler_container_service.add_task_definition("worker-task", 1024, 2048)
        # add container with appropriate task-definition for this task.
        scheduler_container_service.add_container("scheduler-container",
                                                  base_image,
                                                  environment=env,
                                                  logging=log_driver,
                                                  command=["scheduler"])
        # configure scheduler as a service.
        scheduler_container_service.fargate_service("scheduler-service", cluster)
        # Open ports for the service should be last step

        '''
        Create Rabbit-MQ as a message broker for Airflow.
        '''
        message_broker_container_service = ContainerService(airflow_stack)
        # create a task definition for message broker service.
        message_broker_container_service.add_task_definition("message-broker-task", 1024, 2048)
        message_broker_container_service.add_container("rabbitmq_container",
                                                       ecs.ContainerImage.from_registry("rabbitmq:management"),
                                                       environment=env,
                                                       logging=log_driver,
                                                       health_check=ecs.HealthCheck(
                                                           command=["CMD", "rabbitmqctl", "status"])

                                                       )
        message_broker_container_service.add_container_port(5672)
        message_broker_container_service.add_container_port(15672)
        # configure scheduler as a service.
        message_broker_container_service.fargate_service("message_broker_service", cluster)
        # enable cloud map for service discovery. Not sure why we do this.
        message_broker_container_service.enable_service_dicovery(message_broker_service_name)
        # Add target groups for the load balancer
        message_broker_container_service.register_lb_targets(ecs.ListenerConfig.application_listener(rabbitmq_listener),
                                                             "rabbitmq-management-tg",
                                                             message_broker_container_service.container.container_name,
                                                             15672)

        # TODO : make this better.
        rabbitmq_alb.connections.allow_to(
            message_broker_container_service.service.connections,
            ec2.Port.tcp(15672),
            description="allow connection to rabbit-mq management api"
        )

        # open ports for web-service, worker-service and scheduler
        container_services = [web_container_service,
                              scheduler_container_service,
                              worker_container_service]

        for container_service in container_services:

            container_service.service_allow_connection(rds_instance,
                                                       ec2.Port.tcp(5432),
                                                       description="allow connection to RDS")

            container_service.service_allow_connection(message_broker_service.connections,
                                                       ec2.Port.tcp(5672),
                                                       description="allow connection to rabbit-mq broker")

            container_service.service_allow_connection(message_broker_service.connections,
                                                       ec2.Port.tcp(15672),
                                                       description="allow connection to rabbit-mq management api")


class ContainerService:
    def __init__(self, stack):
        self.stack = stack
        self.task_definition = None
        self.container = None
        self.service = None

    def register_lb_targets(self, listener, new_target_group_id, container_name, container_port):
        self.service.register_load_balancer_targets(ecs.EcsTarget(new_target_group_id=new_target_group_id,
                                                                  listener=listener,
                                                                  container_name=container_name,
                                                                  container_port=container_port))

    def add_task_definition(self, id, cpu, memory_limit_mib):
        self.task_definition = ecs.FargateTaskDefinition(
            self.stack,
            id,
            cpu=cpu,
            memory_limit_mib=memory_limit_mib)

    def add_container(self, id, image, environment=None, command=None, logging=None, port=None, health_check=None):
        self.container = self.task_definition.add_container(
            id,
            image=image,
            environment=environment,
            logging=logging,
            command=command,
            health_check=health_check
        )
        # add port mappings if port is not none
        if port is not None:
            self.container.add_port_mappings(ecs.PortMapping(container_port=port))

    def add_container_port(self, port):
        self.container.add_port_mappings(ecs.PortMapping(container_port=port))

    def fargate_service(self, id, cluster, is_alb=False, desired_count=None):
        if self.task_definition is None:
            raise ValueError("Task definition needs to be set")  # TODO: make this better.

        if desired_count is None:
            desired_count = 1

        if is_alb is False:
            self.service = ecs.FargateService(
                self.stack,
                id,
                task_definition=self.task_definition,
                cluster=cluster,
                desired_count=desired_count
            )

        else:
            alb = ecs_patterns.ApplicationLoadBalancedFargateService(
                self.stack,
                id,
                task_definition=self.task_definition,
                protocol=elb.ApplicationProtocol.HTTP,
                cluster=cluster,
                desired_count=desired_count
            )
            self.service = alb.service



    def set_memory_utilization(self,
                               max_capacity,
                               id,
                               target_utilization_percent,
                               policy_name=None,
                               scale_in_cooldown=None,
                               scale_out_cooldown=None):
        self.service.auto_scale_task_count(max_capacity=max_capacity) \
            .scale_on_memory_utilization(id, policy_name=policy_name,
                                         target_utilization_percent=target_utilization_percent,
                                         scale_in_cooldown=core.Duration.seconds(
                                             scale_in_cooldown),
                                         scale_out_cooldown=core.Duration.seconds(
                                             scale_out_cooldown))

    def set_cpu_utilization(self,
                            max_capacity,
                            id,
                            target_utilization_percent,
                            policy_name=None,
                            scale_in_cooldown=None,
                            scale_out_cooldown=None):
        self.service.auto_scale_task_count(max_capacity=max_capacity) \
            .scale_on_cpu_utilization(id, policy_name=policy_name,
                                      target_utilization_percent=target_utilization_percent,
                                      scale_in_cooldown=core.Duration.seconds(
                                          scale_in_cooldown),
                                      scale_out_cooldown=core.Duration.seconds(
                                          scale_out_cooldown))

    def service_allow_connection(self, other_service, port, description=None):
        self.service.connections.allow_to(other_service, port, description=description)

    def enable_service_dicovery(self, name):
        self.service.enable_cloud_map(name=name)


app = core.App()
AirflowFargate(app, "AirflowFargate")
app.synth()
