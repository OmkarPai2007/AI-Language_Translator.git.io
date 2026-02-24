Project by

[Shweta Pandya] (https://github.com/shwetapandya31)

[Omkar Pai] (https://github.com/OmkarPai2007)

This Is an AI language translator, Image generator and analyzer which is made for college project

Commands to push code in Git-Hub

git init
git add .
git commit -m ""
git pull --rebase origin main
git push origin main

The SQL code

CREATE TABLE users2 (
    id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    pass VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE users2
ADD COLUMN translation_limit INT DEFAULT 3,
ADD COLUMN translation_used INT DEFAULT 0;
ALTER TABLE users2 ADD messages_left INT DEFAULT 3;

select * from users2;
