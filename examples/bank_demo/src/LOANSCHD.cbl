       IDENTIFICATION DIVISION.
       PROGRAM-ID. LOANSCHD.
      *================================================================*
      * LOANSCHD - LOAN AMORTIZATION SCHEDULE                          *
      * INPUT (STDIN): 'L' RECORDS. FOR EACH LOAN: PAYMENT (AMORT),    *
      * REFINANCE ELIGIBILITY (LOANCHK) AND THE FIRST N SCHEDULE ROWS. *
      * THE FINAL MONTH PAYS OFF WHATEVER BALANCE REMAINS.             *
      *================================================================*
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT IN-FILE ASSIGN TO KEYBOARD
               ORGANIZATION IS LINE SEQUENTIAL.
       DATA DIVISION.
       FILE SECTION.
       FD  IN-FILE.
       01  IN-LOAN.
           05 IL-REC-TYPE          PIC X.
              88 IL-IS-LOAN        VALUE 'L'.
           05 IL-LOAN-ID           PIC X(8).
           05 IL-PRINCIPAL         PIC 9(7)V99.
           05 IL-RATE              PIC 9V9(5).
           05 IL-TERM              PIC 9(3).
           05 IL-SHOW              PIC 9(2).
           05 IL-STATUS            PIC X.
              88 IL-STATUS-VALUES  VALUE 'A' 'F' 'C'.
           05 IL-OPEN-DATE         PIC 9(8).
       WORKING-STORAGE SECTION.
       COPY LOANPARM.
       COPY ACCTREC.
       COPY LCHKPARM.
       COPY FMTPARM.
       01  WS-EOF                  PIC X VALUE 'N'.
       01  WS-REC-NO               PIC 9(5) VALUE 0.
       01  WS-LOANS                PIC 9(3) VALUE 0.
       01  WS-MRATE                PIC 9V9(9) COMP-3.
       01  WS-BAL                  PIC S9(7)V99 COMP-3.
       01  WS-INT                  PIC S9(7)V99 COMP-3.
       01  WS-PRIN                 PIC S9(7)V99 COMP-3.
       01  WS-PAY                  PIC S9(7)V99 COMP-3.
       01  WS-MONTH                PIC 9(3).
       01  WS-LAST                 PIC 9(3).
       01  OUT-HEADER.
           05 FILLER               PIC X(5) VALUE 'LOAN '.
           05 OH-ID                PIC X(8).
           05 FILLER               PIC X(5) VALUE ' PMT='.
           05 OH-PMT               PIC X(20).
           05 FILLER               PIC X(6) VALUE ' RATE='.
           05 OH-RATE              PIC X(8).
           05 FILLER               PIC X(6) VALUE ' REFI='.
           05 OH-REFI              PIC X.
           05 FILLER               PIC X VALUE SPACE.
           05 OH-REASON            PIC X(20).
       01  OUT-ROW.
           05 FILLER               PIC X(2) VALUE SPACES.
           05 OW-MONTH             PIC ZZ9.
           05 FILLER               PIC X VALUE SPACE.
           05 OW-PAY               PIC ZZZ,ZZ9.99.
           05 FILLER               PIC X VALUE SPACE.
           05 OW-INT               PIC ZZZ,ZZ9.99.
           05 FILLER               PIC X VALUE SPACE.
           05 OW-PRIN              PIC -ZZZ,ZZ9.99.
           05 FILLER               PIC X VALUE SPACE.
           05 OW-BAL               PIC -Z,ZZZ,ZZ9.99.
       01  OUT-ERROR.
           05 FILLER               PIC X(5) VALUE 'LOAN '.
           05 OE-ID                PIC X(8).
           05 FILLER               PIC X(10) VALUE ' ERROR RC='.
           05 OE-RC                PIC 99.
       01  OUT-FOOTER.
           05 FILLER               PIC X(17) VALUE '  TOTAL INTEREST '.
           05 OF-INT               PIC X(20).
       01  OUT-BAD.
           05 FILLER               PIC X(16) VALUE 'BAD LOAN RECORD '.
           05 OB-REC-NO            PIC 9(5).
       PROCEDURE DIVISION.
       0000-MAIN.
           OPEN INPUT IN-FILE
           PERFORM UNTIL WS-EOF = 'Y'
               READ IN-FILE
                   AT END
                       MOVE 'Y' TO WS-EOF
                   NOT AT END
                       ADD 1 TO WS-REC-NO
                       PERFORM 1000-PROCESS-LOAN
               END-READ
           END-PERFORM
           CLOSE IN-FILE
           DISPLAY 'LOANS PROCESSED ' WS-LOANS
           STOP RUN.

       1000-PROCESS-LOAN.
           IF NOT IL-IS-LOAN OR IL-PRINCIPAL NOT NUMERIC
              OR IL-RATE NOT NUMERIC OR IL-TERM NOT NUMERIC
              OR IL-SHOW NOT NUMERIC OR IL-OPEN-DATE NOT NUMERIC
               MOVE WS-REC-NO TO OB-REC-NO
               DISPLAY OUT-BAD
           ELSE
               MOVE IL-PRINCIPAL TO LP-PRINCIPAL
               MOVE IL-RATE TO LP-ANNUAL-RATE
               MOVE IL-TERM TO LP-TERM-MONTHS
               CALL 'AMORT' USING LOAN-PARMS
               IF LP-RETURN-CODE NOT = 0
                   MOVE IL-LOAN-ID TO OE-ID
                   MOVE LP-RETURN-CODE TO OE-RC
                   DISPLAY OUT-ERROR
               ELSE
                   ADD 1 TO WS-LOANS
                   PERFORM 1100-HEADER
                   PERFORM 1200-SCHEDULE
                   MOVE 'A' TO FM-STYLE
                   MOVE LP-TOTAL-INTEREST TO FM-AMOUNT
                   CALL 'FMTUTIL' USING FMT-PARMS
                   MOVE FM-OUT TO OF-INT
                   DISPLAY OUT-FOOTER
               END-IF
           END-IF.

       1100-HEADER.
           MOVE IL-LOAN-ID TO ACCT-ID
           MOVE SPACES TO ACCT-NAME
           MOVE 'L' TO ACCT-TYPE
           COMPUTE ACCT-BALANCE = 0 - IL-PRINCIPAL
           MOVE IL-RATE TO ACCT-RATE
           MOVE IL-OPEN-DATE TO ACCT-OPEN-DATE
           MOVE IL-STATUS TO ACCT-STATUS
           MOVE 'R' TO LC-FUNCTION
           CALL 'LOANCHK' USING ACCT-REC LCHK-PARMS
           MOVE IL-LOAN-ID TO OH-ID
           MOVE 'B' TO FM-STYLE
           MOVE LP-PAYMENT TO FM-AMOUNT
           CALL 'FMTUTIL' USING FMT-PARMS
           MOVE FM-OUT TO OH-PMT
           MOVE 'P' TO FM-STYLE
           MOVE IL-RATE TO FM-RATE
           CALL 'FMTUTIL' USING FMT-PARMS
           MOVE FM-OUT TO OH-RATE
           MOVE LC-RESULT TO OH-REFI
           MOVE LC-REASON TO OH-REASON
           DISPLAY OUT-HEADER.

       1200-SCHEDULE.
           COMPUTE WS-MRATE = IL-RATE / 12
           MOVE IL-PRINCIPAL TO WS-BAL
           IF IL-SHOW < IL-TERM
               MOVE IL-SHOW TO WS-LAST
           ELSE
               MOVE IL-TERM TO WS-LAST
           END-IF
           PERFORM VARYING WS-MONTH FROM 1 BY 1
                   UNTIL WS-MONTH > WS-LAST
               COMPUTE WS-INT ROUNDED = WS-BAL * WS-MRATE
               IF WS-MONTH = IL-TERM
                   MOVE WS-BAL TO WS-PRIN
                   COMPUTE WS-PAY = WS-PRIN + WS-INT
               ELSE
                   COMPUTE WS-PRIN = LP-PAYMENT - WS-INT
                   MOVE LP-PAYMENT TO WS-PAY
               END-IF
               SUBTRACT WS-PRIN FROM WS-BAL
               MOVE WS-MONTH TO OW-MONTH
               MOVE WS-PAY TO OW-PAY
               MOVE WS-INT TO OW-INT
               MOVE WS-PRIN TO OW-PRIN
               MOVE WS-BAL TO OW-BAL
               DISPLAY OUT-ROW
           END-PERFORM.
