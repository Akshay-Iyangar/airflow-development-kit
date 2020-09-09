

## Todo
1. proper virtual environment setup
2. push the cdk to Artifactory (should the cdk be a docker) (should we push to ecr instead)?

3. should be able to define the resources properly so that we can use it and contribute 
back to app service team some of it.


- How are we planning to control the version (major and minor versions)? 


## Needed only if you want to maintain your own airflow image (Currently not required)
1. Have the latest airflow docker pushed to your ECR account.

    i. Docker login, aws-cli (version 2)
    ```bash
    aws ecr get-login-password \                                   
    --region us-west-2 | docker login \
    --username AWS \
    --password-stdin 973069700476.dkr.ecr.us-west-2.amazonaws.com
    ```
   
    ii. Pull latest docker image from docker hub `docker pull apache/airflow`
    
    iii. Tag docker image `docker tag apache/airflow:latest 973069700476.dkr.ecr.us-west-2.amazonaws.com/airflow`
    
    iv. Push docker to ecr `docker push 973069700476.dkr.ecr.us-west-2.amazonaws.com/airflow`
 

##  Deployment of airflow CDK
1. `npm install -g aws-cdk`
2. Check version of CDK `cdk --version`
3. Change directory to particular airflow construct `cd python/airflow-fargate-ecs`
4. Deploy  **CF Template via CDK** `cdk deploy`
5. Destroy **CF Template via CDK** `cdk destroy`


## How to provision Airflow on ECS using Fargate.
1. 




## Why AWS CDK over AWS CloudFormation

1. No built in logic capabilities and learning curve.

AWS CDK supports popular programming languages, which developers can use to build,
automate and manage infrastructure based on an imperative approach

